import itertools
import logging
import os
from enum import Enum, auto
from multiprocessing.pool import ThreadPool as Pool
from typing import List
from urllib.parse import urlparse, unquote

from cozy.architecture.profiler import timing
from cozy.media.media_detector import MediaDetector, NotAnAudioFile, AudioFileCouldNotBeDiscovered
from cozy.model.library import Library
from cozy.architecture.event_sender import EventSender
from cozy.control.filesystem_monitor import FilesystemMonitor, StorageNotFound
from cozy.ext import inject
from cozy.model.settings import Settings

log = logging.getLogger("importer")


class ScanStatus(Enum):
    STARTED = auto()
    SUCCESS = auto()
    ABORTED = auto()
    FINISHED_WITH_ERRORS = auto()


class Importer(EventSender):
    _fs_monitor: FilesystemMonitor = inject.attr("FilesystemMonitor")
    _settings = inject.attr(Settings)
    _library = inject.attr(Library)

    @timing
    def scan(self):
        logging.info("Starting import")
        self.emit_event_main_thread("scan", ScanStatus.STARTED)

        files_to_scan = self._get_files_to_scan()

        undetected_files = self._execute_import(files_to_scan)

        logging.info("Import finished")
        self.emit_event_main_thread("scan", ScanStatus.SUCCESS)

        if len(undetected_files) > 0:
            logging.info("Some files could not be imported:")
            logging.info(undetected_files)
            self.emit_event_main_thread("import-failed", undetected_files)

    def _execute_import(self, files_to_scan: List[str]):
        undetected_files = set()

        pool = Pool()
        while True:
            media_files = pool.map(self.import_file, itertools.islice(files_to_scan, 100))
            undetected_files.update({file for file in media_files if isinstance(file, str)})
            media_files = {file for file in media_files if not isinstance(file, str)}

            if len(media_files) != 0:
                self._library.insert_many(media_files)
            else:
                break
        pool.close()

        return undetected_files

    def _get_files_to_scan(self) -> List[str]:
        paths_to_scan = self._get_configured_storage_paths()
        files_in_media_folders = self._walk_paths_to_scan(paths_to_scan)
        files_to_scan = self._filter_unchanged_files(files_in_media_folders)

        return files_to_scan

    def _get_configured_storage_paths(self) -> List[str]:
        """From all storage path configured by the user,
        we only want to scan those paths that are currently online and exist."""
        paths = [storage.path
                 for storage
                 in self._settings.storage_locations
                 if not storage.external]

        for storage in self._settings.external_storage_locations:
            try:
                if self._fs_monitor.is_storage_online(storage):
                    paths.append(storage.path)
            except StorageNotFound:
                paths.append(storage.path)

        return [path for path in paths if os.path.exists(path)]

    def _walk_paths_to_scan(self, paths: List[str]) -> List[str]:
        """Get all files recursive inside a directory. Returns absolute paths."""
        for path in paths:
            for directory, subdirectories, files in os.walk(path):
                for file in files:
                    filepath = os.path.join(directory, file)
                    yield filepath

    def _filter_unchanged_files(self, files: List[str]) -> List[str]:
        """Filter all files that are already imported and that have not changed from a list of paths."""
        imported_files = self._library.files

        for file in files:
            if file in imported_files:
                chapter = next(chapter
                               for chapter
                               in self._library.chapters
                               if chapter.file == file)

                if int(os.path.getmtime(file)) > chapter.modified:
                    yield file

                continue

            yield file

    def _get_file_count_in_dir(self, dir):
        len([name for name in os.listdir(dir) if os.path.isfile(name)])

    def import_file(self, path: str):
        if not os.path.isfile(path):
            return None

        media_detector = MediaDetector(path)
        try:
            media_data = media_detector.get_media_data()
        except NotAnAudioFile as e:
            return None
        except AudioFileCouldNotBeDiscovered as e:
            return unquote(urlparse(str(e)).path)

        return media_data
