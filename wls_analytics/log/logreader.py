# -*- coding: utf-8 -*-
# @author: Tomas Vitvar, https://vitvar.com, tomas.vitvar@oracle.com

import os
import re
from datetime import datetime
from typing import Iterator, Any, Type, List, Callable
import logging

from ..utils import IndexWordGenerator
from ..config import DATA_DIR


import struct
import pickle
import gzip

from abc import ABC, abstractmethod

try:
    SESSION_ID = os.getsid(os.getpid())
except:
    SESSION_ID = 0

INDEX_DIR = os.path.join(DATA_DIR, "index")
INDEXFILE = os.path.join(INDEX_DIR, f"wlsa-{SESSION_ID}.index")


def cleanup_indexdir(max_age=5):
    if os.path.exists(INDEX_DIR):
        for f in os.listdir(INDEX_DIR):
            if f.endswith(".index"):
                fname = os.path.join(INDEX_DIR, f)
                mtime = os.path.getmtime(fname)
                mod_time = datetime.fromtimestamp(mtime)
                age = datetime.now() - mod_time
                if age.days > max_age:
                    os.remove(fname)


class LabelParser:
    def __init__(self, parsers_def, sets):
        def _make_label_function(label):
            def _x(m):
                try:
                    return label.format(*list([""] + list(m.groups())))
                except Exception as e:
                    return "__internal_error__"

            def label_function(m):
                return _x(m)

            return label_function

        self.parser = {}
        for parser_def in parsers_def:
            if any(item in parser_def["sets"] for item in sets):
                for key, rules in parser_def["rules"].items():
                    if key not in self.parser:
                        self.parser[key] = []
                    for rule in rules:
                        self.parser[key].append(
                            {"pattern": rule["pattern"], "value": _make_label_function(rule["value"])}
                        )

    def __call__(self, payloads):
        data = {}
        if len(self.parser) > 0:
            data = {}
            for key, rules in self.parser.items():
                data[key] = None
                for rule in rules:
                    for payload in payloads:
                        m = next(re.finditer(rule["pattern"], payload, re.MULTILINE), None)
                        if m:
                            data[key] = rule["value"](m) if callable(rule["value"]) else rule["value"]
                            break
                    if data[key] is not None:
                        break
        return data


class LogEntry(ABC):
    """
    A class representing a single log entry.
    """

    def __init__(self, datetime_format: str) -> None:
        """
        Create a new log entry.
        """
        self.datetime_format = datetime_format
        self._time = None
        self._message = None
        self.lines = []
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
    def time(self) -> datetime:
        """
        Return the time of the log entry.
        """
        return self._time

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

    def to_dict(self, label_parser=None):
        return dict(
            time=self.time,
        )


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

    def create_entry(self) -> LogEntry:
        """
        Create a new log entry.
        """
        return self.logentry_class(self.datetime_format)

    def get_datetime(self, first: bool, chunk_size: int = 1024, encoding: str = "utf-8") -> (datetime, int):
        """
        Get the first log entry date and position in the log file.
        """
        self.open()
        num_negatives = 0
        _entry = self.create_entry()
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
        _entry = self.create_entry()
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
                _entry = self.create_entry()
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

    @abstractmethod
    def read_entries(
        self,
        time_from: datetime = None,
        start_pos: int = None,
        time_to: datetime = None,
        chunk_size=1024,
        progress=None,
    ) -> Iterator[LogEntry]:
        pass


class EntryIndex:
    def __init__(
        self,
        time_from: datetime = None,
        time_to: datetime = None,
        set_name: str = None,
        indexfile=None,
        compress=True,
    ):
        self.time_from = time_from
        self.time_to = time_to
        self.set_name = set_name
        self._compress = compress
        self.items = {}
        self.indexfile = indexfile if indexfile is not None else INDEXFILE
        self.generator = IndexWordGenerator()

    def create_item(self, logfile, message):
        index_item = dict(
            id=None,
            message=message,
        )
        index_item["id"] = next(self.generator)
        if logfile not in self.items:
            self.items[logfile] = []
        self.items[logfile].append(self.compress(index_item))
        return index_item["id"]

    def compress(self, item):
        if self._compress and "message" in item:
            item["message"] = gzip.compress(item["message"].encode("utf-8"))
        return item

    def decompress(self, item):
        if self._compress and "message" in item:
            item["message"] = gzip.decompress(item["message"]).decode("utf-8")
        return item

    def search(self, id):
        for logfile, items in self.items.items():
            for item in items:
                if item["id"] == id:
                    return dict(logfile=logfile, data=self.decompress(item))
        return None

    def _write_header(self, f):
        fixed_length = 20
        f.write(struct.pack("d", self.time_from.timestamp()))
        f.write(struct.pack("d", self.time_to.timestamp()))
        f.write(self.set_name.encode("utf-8")[:fixed_length].ljust(fixed_length, b"\0"))

    def _read_header(self, f):
        time_from = datetime.fromtimestamp(struct.unpack("d", f.read(8))[0])
        time_to = datetime.fromtimestamp(struct.unpack("d", f.read(8))[0])
        set_name = f.read(20).decode("utf-8").strip().rstrip("\0")
        return dict(
            time_from=time_from,
            time_to=time_to,
            set_name=set_name,
        )

    def write(self):
        _dir = os.path.dirname(self.indexfile)
        if _dir != "" and not os.path.exists(_dir):
            os.makedirs(_dir, exist_ok=True)
        with open(self.indexfile, "wb") as f:
            self._write_header(f)
            pickle.dump(self, f)

    def read(self):
        if not os.path.exists(self.indexfile):
            raise FileNotFoundError(f"Index file {self.indexfile} does not exist.")
        with open(self.indexfile, "rb") as f:
            self._read_header(f)
            index = pickle.load(f)
        self.items = index.items

    def read_header(self):
        if not os.path.exists(self.indexfile):
            return None
        with open(self.indexfile, "rb") as f:
            return self._read_header(f)

    def output(self, item):
        meta = dict(
            index_id=item["data"]["id"],
            log_file=item["logfile"],
            index_file=self.indexfile,
        )
        return "\n".join([f"{k:<12}: {v}" for k, v in meta.items()] + [""] + [item["data"]["message"]]).encode("utf-8")
