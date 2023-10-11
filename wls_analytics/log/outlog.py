# -*- coding: utf-8 -*-
# @author: Tomas Vitvar, https://vitvar.com, tomas@vitvar.com

import re
import os
import gzip
from .logreader import LogEntry, LogReader
import logging
import struct
import time

from ..utils import IndexWordGenerator
from ..config import DATA_DIR

from typing import Iterator, Tuple

from datetime import datetime, timedelta
import sys

import pickle


DEFAULT_DATETIME_FORMAT = "%b %d, %Y %I:%M:%S,%f %p UTC"

FLOW_ID_PATTERN = re.compile(r"FlowId[:=]\s*(\d+)")
EXCEPTION_PATTERN = re.compile(r"\b([a-zA-Z\.0-9]+?\.[a-zA-Z0-9]+?Exception)(?!\()\b", re.MULTILINE)

COMPONENT_PATTERN = re.compile(r"ComponentDN: ([\w]+)\/([\w]+)\!([0-9\.]+).*?\/([\w]+)", re.MULTILINE)
SECONDS_PATTERN = re.compile(r"seconds since begin=([0-9]+).+seconds left=([0-9]+)", re.MULTILINE)

try:
    SESSION_ID = os.getsid(os.getpid())
except:
    SESSION_ID = 0

INDEX_DIR = os.path.join(DATA_DIR, "index")
SOAERRORS_INDEXFILE = os.path.join(INDEX_DIR, f"wlsa-soaerrors-{SESSION_ID}.index")


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


def list_files(folders, filename_matcher):
    _files = {}
    for folder in folders:
        for p, _, files in os.walk(folder):
            for f in files:
                fname = os.path.join(p, f)
                m = filename_matcher(fname)
                if m is not None:
                    server_name = m.group(1)
                    if server_name not in _files:
                        _files[server_name] = []
                    _files[server_name].append(fname)
    return _files


def get_files(folders, time_from, time_to, filename_matcher, datetime_format=DEFAULT_DATETIME_FORMAT):
    _files = {}
    for server_name, files in list_files(folders, filename_matcher).items():
        for fname in files:
            end_pos = os.path.getsize(fname)  # this is not precise but for our purposes it is ok
            reader = LogReader(fname, datetime_format=datetime_format, logentry_class=OutLogEntry)
            start_pos1, dt1, is_min1 = reader.find(time_from)
            min_dt1, max_dt1 = dt1 if is_min1 else None, dt1 if not is_min1 else None
            if start_pos1 < 0:
                if min_dt1 is not None and time_to < min_dt1:
                    continue
                if max_dt1 is not None and time_from > max_dt1:
                    continue
                start_pos2, dt2, is_min2 = reader.find(time_to)
                min_dt2, max_dt2 = dt2 if is_min2 else None, dt2 if not is_min2 else None
                if start_pos2 < 0:
                    if min_dt2 is not None and time_to < min_dt2:
                        continue
                    if max_dt2 is not None and time_from > max_dt2:
                        continue
                    end_pos = os.path.getsize(fname)
                else:
                    end_pos = start_pos2
                start_pos = 0
            else:
                start_pos = start_pos1

            if server_name not in _files:
                _files[server_name] = []
            _files[server_name].append({"file": fname, "start_pos": start_pos, "end_pos": end_pos, "data": []})
    return _files


class OutLogEntry(LogEntry):
    """
    A class representing a single log entry.
    """

    def __init__(self, pos: int, datetime_format: str = DEFAULT_DATETIME_FORMAT) -> None:
        """
        Create a new log entry.
        """
        super().__init__(pos, datetime_format)
        self.type = None
        self.component = None
        self.bea_code = None
        self.startinx_payload = 0
        self.exception = None
        self._payload = None

    def line_parser(self, line: str) -> Iterator[Tuple[str, int]]:
        pos = 0
        while pos < len(line):
            pos1 = line.find("<", pos)
            if pos1 != -1:
                pos2 = line.find(">", pos1 + 1)
                if pos2 != -1:
                    pos = pos2 + 1
                    yield line[pos1 + 1 : pos2], pos
                else:
                    break
            else:
                break

    def parse_datetime(self, line) -> datetime:
        """
        Parse the datetime from the log entry.
        """
        try:
            return datetime.strptime(next(self.line_parser(line))[0], self.datetime_format)
        except ValueError:
            return None
        except StopIteration:
            return None

    def parse_header(self, line) -> bool:
        """
        Parse the header of the log entry.
        """
        try:
            parser = self.line_parser(line)
            self.time = datetime.strptime(next(parser)[0], self.datetime_format)
            self.type = next(parser)[0]
            self.component = next(parser)[0]
            self.bea_code, self.startinx_payload = next(parser)
            self.add_line(line)
            return True
        except ValueError:
            return False
        except StopIteration:
            return False

    @property
    def payload(self):
        """
        Return the payload of the log entry. The payload is the message without the header.
        """
        if self._message is None or self._payload is None:
            self._payload = self.message
            if len(self._payload) > self.startinx_payload:
                self._payload = self._payload[self.startinx_payload :]
        return self._payload

    def finish(self) -> None:
        """
        This method is called when the log entry is complete.
        """
        exs = []
        m = re.finditer(EXCEPTION_PATTERN, self.payload)
        for match in m:
            exs.append(match.group(1).split(".")[-1])
        if len(exs) > 0:
            self.exception = ",".join(set(exs))


class SOAOutLogEntry(OutLogEntry):
    def __init__(self, pos, datetime_format: str = DEFAULT_DATETIME_FORMAT) -> None:
        """
        Create a new SOA log entry.
        """
        super().__init__(pos, datetime_format)
        self._flow_id = None

    @property
    def flow_id(self):
        if self._flow_id is None:
            m = FLOW_ID_PATTERN.search(self.payload)
            if m is not None:
                self._flow_id = m.group(1)
        return self._flow_id


class SOAGroupEntry:
    def __init__(self, entry, ext_parser=None, index=None, logfile=None) -> None:
        self.entries = []
        self.first_time = None
        self.last_time = None
        self.modified = False
        self.add_entry(entry)
        self.ext_parser = ext_parser
        self._dn = None
        self._seconds = None
        self.index = index
        self.logfile = logfile
        self._ext_data = None
        self.log = logging.getLogger("soa-group-entry")

    def add_entry(self, entry) -> bool:
        if len(self.entries) == 0 or self.entries[0].flow_id == entry.flow_id:
            if self.first_time is None or self.first_time > entry.time:
                self.first_time = entry.time
            if self.last_time is None or self.last_time < entry.time:
                self.last_time = entry.time
            self.entries.append(entry)
            self.modified = True
            return True
        else:
            return False

    def _parse_dn(self):
        if self._dn is None:
            for e in self.entries:
                try:
                    match = next(re.finditer(COMPONENT_PATTERN, e.payload))
                    self._dn = dict(
                        partition=match.group(1),
                        composite=match.group(2),
                        version=match.group(3),
                        component=match.group(4),
                    )
                    return
                except StopIteration:
                    continue

    def parse_seconds(self):
        if self._seconds is None:
            for e in self.entries:
                try:
                    match = next(re.finditer(SECONDS_PATTERN, e.payload))
                    self._seconds = dict(
                        begin=int(match.group(1)),
                        left=int(match.group(2)),
                    )
                    return
                except StopIteration:
                    continue

    @property
    def composite(self):
        self._parse_dn()
        return self._dn["composite"] if self._dn is not None else None

    @property
    def partition(self):
        self._parse_dn()
        return self._dn["partition"] if self._dn is not None else None

    @property
    def version(self):
        self._parse_dn()
        return self._dn["version"] if self._dn is not None else None

    @property
    def component(self):
        self._parse_dn()
        return self._dn["component"] if self._dn is not None else None

    @property
    def seconds_begin(self):
        self.parse_seconds()
        return self._seconds["begin"] if self._seconds is not None else None

    @property
    def seconds_left(self):
        self.parse_seconds()
        return self._seconds["left"] if self._seconds is not None else None

    @property
    def time(self):
        return self.entries[0].time

    @property
    def flow_id(self):
        return self.entries[0].flow_id

    @property
    def timespan(self):
        return self.last_time - self.first_time

    @property
    def ext_data(self):
        if self._ext_data is None and self.ext_parser is not None:
            self._ext_data = {}
            for key, rules in self.ext_parser.items():
                self._ext_data[key] = None
                for rule in rules:
                    for e in self.entries:
                        m = next(re.finditer(rule["pattern"], e.payload, re.MULTILINE), None)
                        if m:
                            self._ext_data[key] = rule["value"](m) if callable(rule["value"]) else rule["value"]
                            break
                    if self._ext_data[key] is not None:
                        break
        return self._ext_data

    def to_dict(self):
        d = dict(
            time=self.time,
            flow_id=self.flow_id,
            timespan=self.timespan,
            num_entries=len(self.entries),
            composite=self.composite,
            version=self.version,
            component=self.component,
            seconds_begin=self.seconds_begin,
            seconds_left=self.seconds_left,
            index=self.index.create_item(self, self.logfile) if self.index is not None else None,
        )
        for k, v in self.ext_data.items():
            if k not in d:
                d[k] = v
            else:
                self.log.warning(f"Duplicate key {k} in SOAGroupEntry.to_dict()")
        return d


class SOAGroupIndex:
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
        self.indexfile = indexfile if indexfile is not None else SOAERRORS_INDEXFILE
        self.generator = IndexWordGenerator()

    def create_item(self, group, logfile):
        index_item = dict(
            id=None,
            composite=group.composite,
            version=group.version,
            messages=[e.message for e in group.entries],
        )
        index_item["id"] = next(self.generator)
        if logfile not in self.items:
            self.items[logfile] = []
        self.items[logfile].append(self.compress(index_item))
        return index_item["id"]

    def compress(self, item):
        if self._compress and "messages" in item:
            item["messages"] = [gzip.compress(m.encode("utf-8")) for m in item["messages"]]
        return item

    def decompress(self, item):
        if self._compress and "messages" in item:
            item["messages"] = [gzip.decompress(m).decode("utf-8") for m in item["messages"]]
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

    def read_file_entries(self, file, ext_parser=None, progress=None):
        _entries = []
        for item in self.items[file]:
            for m in self.decompress(item)["messages"]:
                _entry = SOAOutLogEntry(0)
                _entry.lines = m.split("\n")
                _entry.parse_header(_entry.lines[0])
                _entry.finish()
                if progress is not None:
                    progress.update(len(_entry.message))
                _entries.append(_entry)
        return _entries

    def output(self, item):
        meta = dict(
            index_id=item["data"]["id"],
            composite=item["data"]["composite"],
            version=item["data"]["version"],
            log_file=item["logfile"],
            index_file=self.indexfile,
        )
        return "\n".join([f"{k:<12}: {v}" for k, v in meta.items()] + [""] + item["data"]["messages"]).encode("utf-8")


class SOALogReader(LogReader):
    """
    A class for reading SOA log files.
    """

    def __init__(self, soaout_log: str, datetime_format: str = DEFAULT_DATETIME_FORMAT) -> None:
        """
        Create a new SOA log reader.
        """
        super().__init__(soaout_log, datetime_format, SOAOutLogEntry)

    def read_entries(
        self,
        time_from: datetime = None,
        start_pos: int = None,
        time_to: datetime = None,
        overlap=30,
        chunk_size=1024,
        progress=None,
    ) -> Iterator[LogEntry]:
        if time_from is not None and start_pos is not None:
            raise ValueError("Only one of time_from or start_pos should be provided, not both.")

        entries = []
        for entry in self.read(
            time_from=time_from,
            start_pos=start_pos,
            time_to=time_to + timedelta(seconds=overlap),
            chunk_size=chunk_size,
        ):
            entries.append(entry)
            if progress is not None:
                progress.update(len(entry.message))

        return entries

    def group_entries(
        self,
        entries: list,
        time_to: datetime,
        ext_parser=None,
        index=None,
    ) -> Iterator[SOAGroupEntry]:
        """
        Read SOA errors from the log file.
        """

        groups = []
        for entry in entries:
            if entry.flow_id is not None:
                if entry.time <= time_to:
                    group = None
                    for g in groups:
                        if g.add_entry(entry):
                            group = g
                            break
                    if group is None:
                        groups.append(SOAGroupEntry(entry, ext_parser=ext_parser, index=index, logfile=self.logfile))
                else:
                    # add extra entry to the existing group. the entry is beyond the end of the the time range
                    # but it has flow_id of the existing group so we need to include it
                    for g in groups:
                        if entry.flow_id == g.flow_id:
                            g.add_entry(entry)
                            break

        return groups
