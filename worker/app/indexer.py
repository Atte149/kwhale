"""Audio feature extraction using Essentia + lyrics embedding."""
import json
import os
from pathlib import Path

import httpx
import numpy as np
import psycopg2
import psycopg2.extras

from .json_utils import extract_string_list
from . import tagging
from .tagging import clean_tags as _clean_tags, parse_spectro as _parse_spectro
from . import llm_client
from .llm_client import LLMError

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
OMNI_MODEL = os.environ.get("OMNI_MODEL", "mimo-v2-omni")
# Local bge-m3 embedding service (1024-dim), OpenAI-compatible
EMBEDDING_API_URL = os.environ.get("EMBEDDING_API_URL", "http://embedding:8000/v1/embeddings")

try:
    import essentia.standard as es
    ESSENTIA_OK = True
except ImportError:
    ESSENTIA_OK = False


def extract_features(filepath: str) -> dict | None:
    if not ESSENTIA_OK:
        return None
    try:
        audio = es.MonoLoader(filename=filepath, sampleRate=44100)()
        # Peak-normalize (NormalizerEBUR128 isn't available in all essentia builds)
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak > 1e-9:
            audio = audio / peak

        bpm, _, _, _, _ = es.RhythmExtractor2013(method="multifeature")(audio)
        energy = float(es.Energy()(audio))
        loudness = float(es.Loudness()(audio))
        key, scale, key_strength = es.KeyExtractor()(audio)
        danceability, _ = es.Danceability()(audio)

        w = es.Windowing(type="hann")
        spec = es.Spectrum()
        mfcc_algo = es.MFCC(numberCoefficients=11)
        centroid_algo = es.SpectralCentroidTime()

        mfcc_frames, centroid_frames = [], []
        frame_gen = es.FrameGenerator(audio, frameSize=2048, hopSize=1024)
        for frame in frame_gen:
            windowed = w(frame)
            s = spec(windowed)
            _, mfcc_coeffs = mfcc_algo(s)
            mfcc_frames.append(mfcc_coeffs)
            centroid_frames.append(centroid_algo(windowed))

        mfcc_mean = np.mean(mfcc_frames, axis=0) if mfcc_frames else np.zeros(11)
        centroid_mean = float(np.mean(centroid_frames)) if centroid_frames else 0.0

        bpm_norm = min(float(bpm) / 200.0, 1.0)
        energy_norm = min(energy / 1000.0, 1.0)
        valence = float(np.clip(centroid_mean / 5000.0, 0, 1))
        instrumentalness = float(np.clip(1.0 - (mfcc_mean[0] / 100.0), 0, 1))
        loudness_norm = float(np.clip((loudness + 60) / 60.0, 0, 1))

        key_num = {"C":0,"C#":1,"D":2,"D#":3,"E":4,"F":5,
                   "F#":6,"G":7,"G#":8,"A":9,"A#":10,"B":11}.get(key, 0)
        mode_num = 1 if scale == "major" else 0

        features_vector = [
            bpm_norm, energy_norm, valence, instrumentalness,
            float(danceability), loudness_norm, key_num / 11.0, float(mode_num),
            centroid_mean / 10000.0,
        ] + list(float(x) / 100.0 for x in mfcc_mean)

        return {
            "bpm": float(bpm),
            "energy": energy_norm,
            "valence": valence,
            "instrumentalness": instrumentalness,
            "danceability": float(danceability),
            "loudness": loudness_norm,
            "key": key_num,
            "mode": mode_num,
            "features_vector": features_vector[:20],
        }
    except Exception as e:
        print(f"Essentia error for {filepath}: {e}")
        return None


def fetch_lyrics(artist: str, title: str) -> str | None:
    try:
        r = httpx.get(
            "https://lrclib.net/api/get",
            params={"artist_name": artist, "track_name": title},
            timeout=10.0,
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("plainLyrics") or data.get("syncedLyrics")
    except Exception:
        pass
    return None


def embed_text(text: str) -> list[float] | None:
    """Embed text via the local bge-m3 service (1024-dim)."""
    if not text:
        return None
    try:
        r = httpx.post(
            EMBEDDING_API_URL,
            json={"model": "bge-m3", "input": text[:8000]},
            timeout=60.0,
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
    except Exception as e:
        print(f"embed_text failed: {e}")
        return None


def generate_vibe_tags(artist: str, title: str, lyrics: str | None) -> list[str]:
    """Ask the text LLM for 5–8 mood/atmosphere tags for a track.

    Returns a cleaned, de-duplicated tag list, or [] when the model is
    unavailable or the reply can't be parsed. Never raises.
    """
    if not OPENAI_API_KEY:
        return []
    prompt = (
        f"Song: {artist} — {title}\n"
        + (f"Lyrics excerpt:\n{lyrics[:500]}\n\n" if lyrics else "")
        + "Generate 5–8 short descriptive tags for this song's mood, atmosphere, and feel. "
        "Return ONLY a JSON array of strings, no explanation. "
        'Example: ["melancholic","rainy","introspective"]'
    )
    try:
        msg = llm_client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=LLM_MODEL,
            max_tokens=160,
            timeout=30.0,
        )
    except LLMError as e:
        print(f"generate_vibe_tags failed: {e}")
        return []
    content = msg.get("content") or ""
    # The reply is expected to be a bare array; extract_string_list also handles
    # objects like {"tags":[...]} and truncated arrays without leaking keys.
    tags = extract_string_list(content) or extract_string_list(content, key="tags")
    return _clean_tags(tags)


def build_spectrogram_png(filepath: str) -> bytes | None:
    """Render a log-mel spectrogram of ~45s from the track centre to PNG bytes."""
    if not ESSENTIA_OK:
        return None
    try:
        from PIL import Image
        sr = 22050
        audio = es.MonoLoader(filename=filepath, sampleRate=sr)()
        if len(audio) == 0:
            return None
        mid = len(audio) // 2
        seg = audio[max(0, mid - sr * 22): mid + sr * 23]
        w = es.Windowing(type="hann")
        spec = es.Spectrum()
        mel = es.MelBands(numberBands=96, sampleRate=sr, highFrequencyBound=11000)
        frames = []
        for fr in es.FrameGenerator(seg, frameSize=2048, hopSize=1024):
            frames.append(mel(spec(w(fr))))
        if not frames:
            return None
        M = np.log1p(np.array(frames).T)
        M = (M - M.min()) / (M.max() - M.min() + 1e-9)
        img = (M * 255).astype("uint8")[::-1]  # low freq at bottom
        import io
        buf = io.BytesIO()
        Image.fromarray(img).resize((512, 256)).save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        print(f"build_spectrogram failed: {e}")
        return None


def analyze_spectrogram(png: bytes, artist: str, title: str) -> dict:
    """Send the mel-spectrogram image to the omni model for an audio description.

    Returns {"desc": str|None, "tags": list[str]}. Never raises.
    """
    if not OPENAI_API_KEY or not png:
        return {"desc": None, "tags": []}
    import base64
    b64 = base64.b64encode(png).decode()
    prompt = (
        f"Это mel-спектрограмма трека «{artist} — {title}» "
        "(горизонталь = время, вертикаль = частота, ярче = громче). "
        "Опиши звучание по спектру одним предложением (desc) и дай 4-8 коротких "
        "аудио-тегов (плотный бас / яркие верха / тёплый / динамичный и т.п.). "
        'Ответь ТОЛЬКО компактным JSON без рассуждений: '
        '{"desc":"...","tags":["...","..."]}'
    )
    try:
        msg = llm_client.chat_completion(
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
            model=OMNI_MODEL,
            # 800 was too tight and truncated mid-array; 1200 fits desc + tags.
            max_tokens=1200,
            response_format={"type": "json_object"},
            timeout=120.0,
        )
    except LLMError as e:
        print(f"analyze_spectrogram failed: {e}")
        return {"desc": None, "tags": []}
    content = (msg.get("content") or "").strip()
    return _parse_spectro(content)


def _read_all_artists(filepath: str, title: str) -> list[str]:
    """Read all_artists from the FLAC file. Returns [] on any read error.

    VorBis `artists` (multi-value) is the source of truth; the indexer
    only needs the column populated — the heavy parsing logic lives in
    tagging.extract_all_artists and is unit-tested separately.
    """
    try:
        import mutagen
        mf = mutagen.File(filepath, easy=True)
        if mf is None:
            return []
        return tagging.extract_all_artists(
            mf.get("artists"),
            mf.get("artist"),
            mf.get("title") or [title] if title else mf.get("title"),
        )
    except Exception as e:
        print(f"_read_all_artists error for {filepath}: {e}")
        return []


def _record_failure(navidrome_id: str, filepath: str, title: str, artist: str, reason: str) -> None:
    """Persist a 'failed' row so the track is not retried forever.

    index_all_tracks() skips any track that already has a row (ok or failed)
    with attempts past the retry budget, so we must leave a marker here instead
    of returning silently. Re-running an explicit index keeps incrementing
    index_attempts so transient failures still get a few chances.

    All artists (primary + features) are still written to `all_artists` so
    the backfill doesn't have to re-read FLAC for tracks that already
    failed Essentia.
    """
    all_artists = _read_all_artists(filepath, title)
    all_artists_text = " ".join(all_artists)
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO track_features
                        (navidrome_id, filepath, title, artist,
                         all_artists, all_artists_text, artists_indexed_at,
                         index_status, index_error, index_attempts)
                    VALUES (%s,%s,%s,%s,%s,%s,NOW(),'failed',%s,1)
                    ON CONFLICT (navidrome_id) DO UPDATE SET
                        index_status='failed',
                        index_error=EXCLUDED.index_error,
                        index_attempts=track_features.index_attempts + 1,
                        all_artists=EXCLUDED.all_artists,
                        all_artists_text=EXCLUDED.all_artists_text,
                        artists_indexed_at=NOW(),
                        updated_at=NOW()
                    """,
                    (navidrome_id, filepath, title, artist,
                     all_artists, all_artists_text, reason[:500]),
                )
    except Exception as e:
        print(f"_record_failure error for {navidrome_id}: {e}")


def index_track(navidrome_id: str, filepath: str, artist: str, title: str) -> bool:
    features = extract_features(filepath)
    if not features:
        # Persist a 'failed' marker so index_all_tracks won't re-queue this
        # track on every pass (the old behaviour was an infinite retry loop).
        reason = "essentia feature extraction returned no features"
        print(f"No audio features for {navidrome_id} ({filepath}); recording failure")
        _record_failure(navidrome_id, filepath, title, artist, reason)
        return False

    lyrics = fetch_lyrics(artist, title)
    lyrics_embedding = embed_text(lyrics) if lyrics else None
    vibe_tags = generate_vibe_tags(artist, title, lyrics)

    spectro = {"desc": None, "tags": []}
    png = build_spectrogram_png(filepath)
    if png:
        spectro = analyze_spectrogram(png, artist, title)

    duration = None
    all_artists: list[str] = []
    try:
        import mutagen
        mf = mutagen.File(filepath)
        if mf and mf.info:
            duration = mf.info.length
        all_artists = _read_all_artists(filepath, title)
    except Exception:
        pass

    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            vec_str = (
                "[" + ",".join(str(x) for x in features["features_vector"]) + "]"
                if features else None
            )
            emb_str = (
                "[" + ",".join(str(x) for x in lyrics_embedding) + "]"
                if lyrics_embedding else None
            )
            cur.execute(
                """
                INSERT INTO track_features
                    (navidrome_id, filepath, title, artist, duration_sec,
                     all_artists, all_artists_text, artists_indexed_at,
                     bpm, energy, valence, instrumentalness, danceability,
                     loudness, key, mode, features_vector, lyrics, lyrics_embedding, vibe_tags,
                     spectro_desc, spectro_tags, index_status, index_error)
                VALUES (%s,%s,%s,%s,%s, %s,%s,NOW(), %s,%s,%s,%s,%s, %s,%s,%s,%s::vector,%s,%s::vector,%s, %s,%s,'ok',NULL)
                ON CONFLICT (navidrome_id) DO UPDATE SET
                    bpm=EXCLUDED.bpm, energy=EXCLUDED.energy,
                    valence=EXCLUDED.valence, instrumentalness=EXCLUDED.instrumentalness,
                    danceability=EXCLUDED.danceability, loudness=EXCLUDED.loudness,
                    key=EXCLUDED.key, mode=EXCLUDED.mode,
                    features_vector=EXCLUDED.features_vector,
                    all_artists=EXCLUDED.all_artists,
                    all_artists_text=EXCLUDED.all_artists_text,
                    artists_indexed_at=NOW(),
                    lyrics=COALESCE(EXCLUDED.lyrics, track_features.lyrics),
                    lyrics_embedding=COALESCE(EXCLUDED.lyrics_embedding, track_features.lyrics_embedding),
                    vibe_tags=COALESCE(EXCLUDED.vibe_tags, track_features.vibe_tags),
                    spectro_desc=COALESCE(EXCLUDED.spectro_desc, track_features.spectro_desc),
                    spectro_tags=COALESCE(EXCLUDED.spectro_tags, track_features.spectro_tags),
                    index_status='ok', index_error=NULL,
                    updated_at=NOW()
                """,
                (
                    navidrome_id, filepath, title, artist, duration,
                    all_artists, " ".join(all_artists),
                    features.get("bpm") if features else None,
                    features.get("energy") if features else None,
                    features.get("valence") if features else None,
                    features.get("instrumentalness") if features else None,
                    features.get("danceability") if features else None,
                    features.get("loudness") if features else None,
                    features.get("key") if features else None,
                    features.get("mode") if features else None,
                    vec_str,
                    lyrics,
                    emb_str,
                    json.dumps(vibe_tags),
                    spectro["desc"],
                    json.dumps(spectro["tags"]),
                ),
            )
    return True
