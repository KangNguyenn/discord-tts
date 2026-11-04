from __future__ import annotations

import logging
import multiprocessing
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable


logger = logging.getLogger(__name__)


def _preview_process_main(connection, voice_dir: str, output_dir: str) -> None:
    """Process entry point. Keep all Piper/ONNX state isolated from the GTK app."""
    from discord_tts.tts.engine import PiperEngine

    engine = PiperEngine(Path(voice_dir))
    generated_paths: set[Path] = set()
    try:
        while True:
            try:
                request = connection.recv()
            except EOFError:
                break

            action = request.get("action")
            if action == "shutdown":
                break
            if action != "synthesize":
                continue

            request_id = request.get("request_id")
            wav_path: Path | None = None
            try:
                wav_path = engine.synthesize(
                    request["text"],
                    request["model_id"],
                    request.get("speed", 1.0),
                    request.get("volume", 1.0),
                    request.get("speaker_id", 0),
                    preview=True,
                    output_dir=Path(output_dir),
                )
                generated_paths.add(wav_path)
                response = {
                    "request_id": request_id,
                    "ok": True,
                    "wav_path": str(wav_path),
                }
            except Exception as exc:
                logger.exception("Preview worker synthesis failed (request=%s)", request_id)
                response = {
                    "request_id": request_id,
                    "ok": False,
                    "error": str(exc),
                }

            try:
                connection.send(response)
            except (BrokenPipeError, EOFError, OSError):
                if wav_path is not None:
                    wav_path.unlink(missing_ok=True)
                break
    finally:
        engine.release_voice()
        for path in generated_paths:
            path.unlink(missing_ok=True)
        try:
            connection.close()
        except OSError:
            pass


class PreviewWorker:
    """Lazy, reusable process that owns preview-only Piper/ONNX memory."""

    def __init__(
        self,
        voice_dir: Path,
        result_callback: Callable[[dict], None],
    ):
        self._voice_dir = str(voice_dir)
        self._result_callback = result_callback
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.RLock()
        self._process: multiprocessing.Process | None = None
        self._connection = None
        self._reader_thread: threading.Thread | None = None
        self._temp_dir: Path | None = None
        self._generation = 0
        self._request_in_flight = False
        self._closed = False
        self._blocked = False

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._request_in_flight

    def _start_locked(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="discord-tts-preview-"))
        parent_connection, child_connection = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=_preview_process_main,
            args=(child_connection, self._voice_dir, str(temp_dir)),
            name="discord-tts-preview",
            daemon=True,
        )
        try:
            process.start()
        except Exception:
            parent_connection.close()
            child_connection.close()
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        child_connection.close()

        self._temp_dir = temp_dir
        self._generation += 1
        generation = self._generation
        self._process = process
        self._connection = parent_connection
        self._reader_thread = threading.Thread(
            target=self._read_results,
            args=(parent_connection, generation),
            name="preview-result-reader",
            daemon=True,
        )
        self._reader_thread.start()
        logger.info("Started preview worker process (pid=%s)", process.pid)

    def request(
        self,
        request_id: int,
        text: str,
        model_id: str,
        speaker_id: int,
        speed: float,
        volume: float,
    ) -> bool:
        with self._lock:
            if self._closed or self._blocked or self._request_in_flight:
                return False
            if self._process is None or not self._process.is_alive():
                self._stop_process_locked(force=True)
                self._start_locked()

            if self._connection is None:
                return False
            try:
                self._connection.send(
                    {
                        "action": "synthesize",
                        "request_id": request_id,
                        "text": text,
                        "model_id": model_id,
                        "speaker_id": speaker_id,
                        "speed": speed,
                        "volume": volume,
                    }
                )
            except (BrokenPipeError, EOFError, OSError):
                logger.exception("Could not send request to preview worker")
                self._stop_process_locked(force=True)
                return False

            self._request_in_flight = True
            return True

    def _read_results(self, connection, generation: int) -> None:
        while True:
            try:
                message = connection.recv()
            except (EOFError, OSError):
                break

            with self._lock:
                current = generation == self._generation and not self._closed
                if current:
                    self._request_in_flight = False

            if current:
                try:
                    self._result_callback(message)
                except Exception:
                    logger.exception("Preview result callback failed")
            else:
                self._remove_result_file(message)

        with self._lock:
            if generation == self._generation:
                self._request_in_flight = False
                process, self._process = self._process, None
                self._connection = None
                if process is not None:
                    process.join(timeout=0.2)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=0.5)
                    if process.is_alive() and hasattr(process, "kill"):
                        process.kill()
                        process.join(timeout=0.5)
                    if not process.is_alive():
                        process.close()
                temp_dir, self._temp_dir = self._temp_dir, None
                if temp_dir is not None:
                    shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            connection.close()
        except OSError:
            pass

    @staticmethod
    def _remove_result_file(message: dict) -> None:
        wav_path = message.get("wav_path")
        if wav_path:
            Path(wav_path).unlink(missing_ok=True)

    def cancel_current(self) -> None:
        """Cancel an in-flight synthesis but keep the manager reusable."""
        with self._lock:
            if self._request_in_flight:
                self._stop_process_locked(force=True)

    def stop_for_speech(self) -> None:
        """Terminate preview immediately so real Discord TTS has priority."""
        with self._lock:
            self._blocked = True
            self._stop_process_locked(force=True)

    def allow_preview(self) -> None:
        """Allow preview requests again after real Discord TTS becomes idle."""
        with self._lock:
            if not self._closed:
                self._blocked = False

    def close(self) -> None:
        """Permanently close this worker manager when the model dialog closes."""
        with self._lock:
            self._closed = True
            self._stop_process_locked(force=False)

    def _stop_process_locked(self, force: bool) -> None:
        self._generation += 1
        was_busy = self._request_in_flight
        self._request_in_flight = False
        process, self._process = self._process, None
        connection, self._connection = self._connection, None
        self._reader_thread = None

        if process is not None:
            process_id = process.pid
            if process.is_alive():
                if not force and not was_busy and connection is not None:
                    try:
                        connection.send({"action": "shutdown"})
                    except (BrokenPipeError, EOFError, OSError):
                        pass
                    process.join(timeout=0.5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=0.3)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(timeout=0.5)
            else:
                process.join(timeout=0)

        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        temp_dir, self._temp_dir = self._temp_dir, None
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if process is not None:
            if not process.is_alive():
                try:
                    process.close()
                except ValueError:
                    pass
            else:
                logger.error(
                    "Preview worker process did not exit cleanly (pid=%s)",
                    process.pid,
                )
            logger.info("Stopped preview worker process (pid=%s)", process_id)
