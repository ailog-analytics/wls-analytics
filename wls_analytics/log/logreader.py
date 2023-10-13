# -*- coding: utf-8 -*-
# @author: Tomas Vitvar, https://vitvar.com, tomas.vitvar@oracle.com

import os
import re
from datetime import datetime
from typing import Iterator, Any, Type, List, Callable
import logging

from abc import ABC, abstractmethod


class LogEntry(ABC):
    """
    A class representing a single log entry.
    """

    def __init__(self, pos: int, datetime_format: str) -> None:
        """
        Create a new log entry.
        """
        self.datetime_format = datetime_format
        self.time = None
        self._message = None
        self.lines = []
        self.pos = pos
        self.log = logging.getLogger("log-entry")

    def add_line(self, line: str) -> None:
        """
        Add a line to the log entry.
        """
        self.lines.append(line)
        self._message = None

    def finish(self) -> None:
        """
        This method is called when the log entry is complete.
        """
        pass

    @property
    def message(self):
        """
        Return the message of the log entry. The message is the entire log entry as the orginal representation
        retrieved from the log file.
        """
        if self._message is None:
            self._message = "\n".join(self.lines)
        return self._message

    @abstractmethod
    def line_parser(self, line: str) -> Iterator[str]:
        pass

    def parse_datetime(self, line) -> datetime:
        """
        Parse the datetime from the log entry.
        """
        try:
            return datetime.strptime(next(self.line_parser(line)), self.datetime_format)
        except ValueError or StopIteration:
            return None

    @abstractmethod
    def parse_header(self, line) -> bool:
        """
        Parse the header of the log entry.
        """
        pass


class LogReader(ABC):
    """
    A class for reading WLS out logs.
    """

    def __init__(self, logfile: str, datetime_format: str = None, logentry_class: Type[LogEntry] = None) -> None:
        self.handler = None
        self.logentry_class = logentry_class
        self.logfile = logfile
        self.datetime_format = datetime_format
        self.log = logging.getLogger("log-reader")

    def open(self, reopen=False):
        """
        Open the log file for reading.
        """
        if reopen and self.handler is not None:
            self.close()
            self.handler = None
        if self.handler is None:
            self.handler = open(self.logfile, "rb")

    def close(self):
        """
        Close the log file.
        """
        if self.handler is not None:
            self.handler.close()
            self.handler = None

    def __del__(self):
        """
        Destructor. Close the log file if it is open.
        """
        self.close()

    def create_entry(self, pos: int) -> LogEntry:
        """
        Create a new log entry.
        """
        return self.logentry_class(pos, self.datetime_format)

    def get_datetime(self, first: bool, chunk_size: int = 1024, encoding: str = "utf-8") -> (datetime, int):
        """
        Get the first log entry date and position in the log file.
        """
        self.open()
        num_negatives = 0
        _entry = self.create_entry(0)
        next_pos = 0 if first else os.path.getsize(self.logfile) - chunk_size
        while next_pos >= 0 and next_pos < os.path.getsize(self.logfile) and num_negatives < 2:
            self.handler.seek(next_pos)
            chunk = self.handler.read(chunk_size).decode(encoding, errors="replace")
            lines = chunk.split("\n")
            for l in lines:
                dt = _entry.parse_datetime(l)
                if dt is not None:
                    return dt, self.handler.tell()
            next_pos += chunk_size if first else -chunk_size
            if next_pos < 0:
                next_pos = 0
                num_negatives += 1
        return None, None

    def find(self, time: datetime, chunk_size: int = 1024, encoding: str = "utf-8") -> int:
        """
        Find the pos of the entry in the log file where the time of the entry is equal or greater than `time`.
        When the log entry is not found, -1 is returned.
        """

        self.open()
        start = 0
        dt_pos = -1
        last_dt = None
        size = os.path.getsize(self.logfile)
        end = size
        _entry = self.create_entry(0)
        num_lefts, num_rights = 0, 0

        # use binary search to find the first log entry that matches the specified date and time.
        # Since not every line has a datetime, we need to read more lines to find the first one
        # that matches the specified datetime. We read the lines in chunks of chunk_size bytes.
        while (end - start) > 70:
            pos = start + (end - start) // 2
            self.handler.seek(pos)

            first_pos, second_pos = None, None
            chunk_pos = pos

            # read chunks to find the first and second datetime when the first time read is less
            # than time_from and the second is greater than time_from we have found the first position
            count = 0
            while count < 2:
                # read a chunk of data; 70 is the minimum number of bytes to read to get a datetime
                chunk = self.handler.read(min(chunk_size, end - start)).decode(encoding, errors="replace")
                lines = chunk.split("\n")
                current_bytes = 0
                for l in lines:
                    # parse the datetime from the line
                    dt = _entry.parse_datetime(l)
                    if dt is not None:
                        last_dt = dt
                        dt_pos = chunk_pos + current_bytes
                        count += 1
                        if time <= dt:
                            first_pos = chunk_pos
                        if time > dt or chunk_pos == 0:
                            second_pos = chunk_pos
                    if first_pos is not None and second_pos is not None:
                        break
                    current_bytes += len(l) + 1
                chunk_pos += len(chunk)
                if chunk_pos >= end:
                    break
            if first_pos is None and second_pos is None:
                end = pos
            elif first_pos is not None and second_pos is None:
                end = pos
                num_rights += 1
            elif second_pos is not None and first_pos is None:
                start = chunk_pos
                num_lefts += 1
            else:
                break

        if last_dt is not None and num_rights == 0 and time > last_dt:
            return -1, last_dt, False
        if last_dt is not None and num_lefts == 0 and time < last_dt:
            return -1, last_dt, True
        return dt_pos, last_dt, None

    def read(
        self,
        time_from: datetime = None,
        start_pos: int = None,
        time_to: datetime = None,
        count: int = None,
        chunk_size: int = 1024,
        encoding="utf-8",
    ) -> Iterator[LogEntry]:
        """
        Read the log file and return an iterator of log entries. The log entries are returned in the order
        they appear in the log file. The iterator can be limited by the time_from and time_to parameters.
        The count parameter can be used to limit the number of log entries returned.
        """
        if time_from is not None and start_pos is not None:
            raise ValueError("Only one of time_from or start_pos should be provided, not both.")
        if time_from is not None:
            start_pos, _, _ = self.find(time_from, chunk_size)
        if start_pos < 0:
            return
        entry, dt = None, None
        reminder = ""
        _count = 0
        self.open()
        self.handler.seek(start_pos)
        while dt is None or (time_to is None or dt <= time_to):
            current_pos = self.handler.tell()
            chunk = self.handler.read(chunk_size).decode(encoding, errors="replace")
            if len(chunk) == 0:
                break
            lines = chunk.split("\n")
            lines[0] = reminder + lines[0]
            current_pos -= len(reminder)
            has_reminder = chunk[-1] != "\n"
            for inx, l in enumerate(lines[0:-1] if has_reminder else lines[0:]):
                current_pos = current_pos + len(l) + 1
                _entry = self.create_entry(current_pos)
                dt = _entry.time if _entry.parse_header(l) else None
                if dt is not None:
                    if entry is not None:
                        _count += 1
                        entry.finish()
                        yield entry
                        entry = None
                        if count is not None and _count >= count:
                            break
                    if time_to is not None and dt > time_to:
                        break
                    entry = _entry
                elif entry is not None:
                    entry.add_line(l)
            reminder = lines[-1] if has_reminder else ""
        if entry is not None:
            yield entry


class LogStorage:
    def __init__(self, dir) -> None:
        self.dir = dir
        self.entries = []

    def add_entry(self, entry: LogEntry):
        self.entries.append(entry)

    def store(self):
        for entry in self.entries:
            entry.store(self.dir)
