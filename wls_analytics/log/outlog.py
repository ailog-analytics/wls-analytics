# -*- coding: utf-8 -*-
# @author: Tomas Vitvar, https://vitvar.com, tomas@vitvar.com

import re
import os
from .logreader import LogEntry, LogReader, LabelParser
import logging
import sys

from typing import Iterator, Tuple

from datetime import datetime, timedelta


DEFAULT_DATETIME_FORMAT = "%b %d, %Y %I:%M:%S,%f %p UTC"

FLOW_ID_PATTERN = re.compile(r"FlowId[:=]\s*(\d+)")
EXCEPTION_PATTERN = re.compile(r"\b([a-zA-Z\.0-9]+?\.[a-zA-Z0-9]+?Exception)(?!\()\b", re.MULTILINE)

COMPONENT_PATTERN = re.compile(r"ComponentDN: ([\w]+)\/([\w]+)\!([0-9\.]+).*?\/([\w]+)", re.MULTILINE)
SECONDS_PATTERN = re.compile(r"seconds since begin=([0-9]+).+seconds left=([0-9]+)", re.MULTILINE)


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


def get_files(reader_class, folders, time_from, time_to, filename_matcher, datetime_format=DEFAULT_DATETIME_FORMAT):
    _files = {}
    for server_name, files in list_files(folders, filename_matcher).items():
        for fname in files:
            end_pos = os.path.getsize(fname)  # this is not precise but for our purposes it is ok
            reader = reader_class(fname, datetime_format=datetime_format, logentry_class=OutLogEntry)
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

    def __init__(self, datetime_format: str = DEFAULT_DATETIME_FORMAT) -> None:
        """
        Create a new log entry.
        """
        super().__init__(datetime_format)
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
            self._time = datetime.strptime(next(parser)[0], self.datetime_format)
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
    def __init__(self, datetime_format: str = DEFAULT_DATETIME_FORMAT) -> None:
        """
        Create a new SOA log entry.
        """
        super().__init__(datetime_format)
        self._flow_id = None

    @property
    def flow_id(self):
        if self._flow_id is None:
            m = FLOW_ID_PATTERN.search(self.payload)
            if m is not None:
                self._flow_id = m.group(1)
        return self._flow_id


class OSBOutLogEntry(OutLogEntry):
    def __init__(self, datetime_format: str = DEFAULT_DATETIME_FORMAT) -> None:
        """
        Create a new OSB log entry.
        """
        super().__init__(datetime_format)
        self.service = None

    def finish(self) -> None:
        super().finish()
        m = next(re.finditer(r"^\s*<([A-Za-z\/0-9_]+)", self.payload), None)
        if m is not None:
            self.service = m.group(1).split("/")[-1]
            if self.service == "servicebus":
                m = next(re.finditer(r"^\s*<servicebus:([A-Za-z\/0-9_]+)", self.payload), None)
                if m is not None:
                    self.service = m.group(1).split("/")[-1]
        else:
            m = next(re.finditer(r"^\s*<\[service_name: ([A-Za-z\/0-9_]+)\]", self.payload), None)
            if m is not None:
                self.service = m.group(1).split("/")[-1]

    def to_dict(self, label_parser=None):
        d = dict(
            time=self.time,
            component=self.component,
            type=self.type,
            bea_code=self.bea_code,
            service=self.service,
        )
        if label_parser is not None:
            ext_data = label_parser([self.payload])
            for k, v in ext_data.items():
                if k not in d:
                    d[k] = v
                else:
                    self.log.warning(f"Duplicate key {k} in OSBOutLogEntry.to_dict()")
        return d


class SOAGroupEntry(LogEntry):
    def __init__(self, datetime_format: str = DEFAULT_DATETIME_FORMAT) -> None:
        super().__init__(datetime_format)
        self.type = "Error"
        self.entries = []
        self.first_time = None
        self.last_time = None
        self.modified = False
        self._dn = None
        self._seconds = None
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
    def message(self):
        return "\n".join([e.message for e in self.entries])

    def to_dict(self, label_parser=None):
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
        )
        if label_parser is not None:
            ext_data = label_parser([e.payload for e in self.entries])
            for k, v in ext_data.items():
                if k not in d:
                    d[k] = v
                else:
                    self.log.warning(f"Duplicate key {k} in SOAGroupEntry.to_dict()")
        return d

    def line_parser(self, line: str) -> Iterator[str]:
        raise NotImplementedError("SOAGroupEntry.line_parser() is not implemented.")

    def parse_header(self, line) -> bool:
        raise NotImplementedError("SOAGroupEntry.parse_header() is not implemented.")


def format_composite(c, v, e):
    """
    Tabledef formatter for composite name.
    """
    max_len = 35
    if len(v) > max_len and sys.stdout.isatty():
        return v[: max_len - 1] + "…"
    else:
        return v


def format_time(v, datetime_format):
    """
    Tabledef formatter for time.
    """
    return v


class SOALogReader(LogReader):
    """
    A class for reading SOA log files.
    """

    def __init__(self, soaout_log: str, datetime_format: str = DEFAULT_DATETIME_FORMAT, logentry_class=None) -> None:
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
                        g = SOAGroupEntry()
                        g.add_entry(entry)
                        groups.append(g)
                else:
                    # add extra entry to the existing group. the entry is beyond the end of the the time range
                    # but it has flow_id of the existing group so we need to include it
                    for g in groups:
                        if entry.flow_id == g.flow_id:
                            g.add_entry(entry)
                            break

        return groups

    def get_tabledef(self, label_parser=None, add_index=False):
        table_def = [
            {
                "name": "TIME",
                "value": "{time}",
                "format": lambda e, v, c: format_time(v, self.datetime_format),
                "help": "Error time",
            },
            {"name": "SERVER", "value": "{server}", "help": "Server name"},
            {"name": "FLOW_ID", "value": "{flow_id}", "help": "Flow ID"},
            {"name": "COMPOSITE", "value": "{composite}", "format": format_composite, "help": "Composite name"},
        ]
        if label_parser is not None:
            for key in label_parser.parser.keys():
                table_def.append({"name": key.upper(), "value": "{" + key + "}", "help": "Extended attribute"})
        if add_index:
            table_def.append({"name": "INDEX", "value": "{index}", "help": "Index entry ID"})
        return table_def


class OSBLogReader(LogReader):
    """
    A class for reading OSB log files.
    """

    def __init__(self, soaout_log: str, datetime_format: str = DEFAULT_DATETIME_FORMAT, logentry_class=None) -> None:
        """
        Create a new OSB log reader.
        """
        super().__init__(soaout_log, datetime_format, OSBOutLogEntry)

    def read_entries(
        self,
        time_from: datetime = None,
        start_pos: int = None,
        time_to: datetime = None,
        chunk_size=1024,
        progress=None,
    ) -> Iterator[LogEntry]:
        if time_from is not None and start_pos is not None:
            raise ValueError("Only one of time_from or start_pos should be provided, not both.")

        entries = []
        for entry in self.read(
            time_from=time_from,
            start_pos=start_pos,
            time_to=time_to,
            chunk_size=chunk_size,
        ):
            entries.append(entry)
            if progress is not None:
                progress.update(len(entry.message))

        return entries

    def get_tabledef(self, label_parser=None, add_index=False):
        table_def = [
            {
                "name": "TIME",
                "value": "{time}",
                "format": lambda e, v, c: format_time(v, self.datetime_format),
                "help": "Error time",
            },
            {"name": "SERVER", "value": "{server}", "help": "Server name"},
            {
                "name": "COMPONENT",
                "value": "{component}",
                "format": lambda x, v, y: v.split(".")[-1],
                "help": "OSB component",
            },
            {"name": "CODE", "value": "{bea_code}", "help": "BEA error code"},
            {"name": "SERVICE", "value": "{service}", "help": "OSB Service"},
        ]
        for key in label_parser.parser.keys():
            table_def.append({"name": key.upper(), "value": "{" + key + "}", "help": "Extended attribute"})
        if add_index:
            table_def.append({"name": "INDEX", "value": "{index}", "help": "Index entry ID"})
        return table_def
