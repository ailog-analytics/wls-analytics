# -*- coding: utf-8 -*-
# @author: Tomas Vitvar, https://vitvar.com, tomas.vitvar@oracle.com

import click
from datetime import datetime, timedelta
from tqdm import tqdm
import os
import re
import json
import time
import threading
import sys
import subprocess
import operator


from ..log import (
    SOALogReader,
    LogReader,
    OutLogEntry,
    get_files,
    list_files,
    DEFAULT_DATETIME_FORMAT,
    SOAGroupIndex,
    cleanup_indexdir,
)

from ..json2table import Table
from ..config import DATA_DIR

from .click_ext import BaseCommandConfig


class DateTimeOption(click.Option):
    def type_cast_value(self, ctx, value):
        if value is None:
            return None
        for fmt in ["%Y-%m-%d %H:%M:%S", "%H:%M:%S", "%H:%M"]:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                pass
        raise click.BadParameter("use values in the format '%Y-%m-%d %H:%M:%S', '%H:%M:%S' or '%H:%M'.")


class OffsetOption(click.Option):
    def type_cast_value(self, ctx, value):
        if value is None:
            return None
        offset_units = {"h": "hours", "d": "days", "m": "minutes"}
        unit = value[-1]
        if unit in offset_units:
            try:
                value = int(value[:-1])
                return timedelta(**{offset_units[unit]: value})
            except ValueError:
                pass
        raise click.BadParameter("use values like '1h', '2d', '10m'.")


@click.command(cls=BaseCommandConfig, log_handlers=["file"])
@click.argument("set_name", metavar="<SET>", required=True)
def range(config, log, set_name):
    logs_set = config(f"sets.{set_name}")
    if logs_set is None:
        raise Exception(f"The log set '{set_name}' not found in the configuration file.")

    range_data = []
    for server_name, files in list_files(
        logs_set.directories, lambda fname: re.search(logs_set.filename_pattern, fname)
    ).items():
        range_item = {"server": server_name, "min": None, "max": None, "files": len(files), "size": 0}
        range_data.append(range_item)
        for fname in files:
            range_item["size"] += os.path.getsize(fname)
            reader = LogReader(fname, datetime_format=DEFAULT_DATETIME_FORMAT, logentry_class=OutLogEntry)
            first, _ = reader.get_datetime(True)
            last, _ = reader.get_datetime(False)
            if first is not None and (range_item["min"] is None or first < range_item["min"]):
                range_item["min"] = first
            if last is not None and (range_item["max"] is None or last > range_item["max"]):
                range_item["max"] = last

    range_data = sorted(range_data, key=lambda x: x["server"])
    table_def = [
        {"name": "SERVER", "value": "{server}", "help": "Server name"},
        {"name": "FILES", "value": "{files}", "help": "Number of files"},
        {
            "name": "SIZE [GB]",
            "value": "{size}",
            "format": lambda _, v, y: round(v / 1024 / 1024 / 1024, 2) if v is not None else 0,
            "help": "Total size",
        },
        {
            "name": "MIN",
            "value": "{min}",
            "format": lambda _, v, y: v.replace(microsecond=0) if v is not None else "n/a",
            "help": "Minimum datetime",
        },
        {
            "name": "MAX",
            "value": "{max}",
            "format": lambda _, v, y: v.replace(microsecond=0) if v is not None else "n/a",
            "help": "Maximum datetime",
        },
    ]
    Table(table_def, None, False).display(range_data)


def make_label_function(label):
    def _x(m):
        try:
            return label.format(*list([""] + list(m.groups())))
        except Exception as e:
            return "__internal_error__"

    def label_function(m):
        return _x(m)

    return label_function


def load_parser(parsers_def, sets: list):
    _parser = {}
    for parser_def in parsers_def:
        if any(item in parser_def["sets"] for item in sets):
            for key, rules in parser_def["rules"].items():
                if key not in _parser:
                    _parser[key] = []
                for rule in rules:
                    _parser[key].append({"pattern": rule["pattern"], "value": make_label_function(rule["value"])})
    return _parser


def format_composite(v, max_len=35):
    if len(v) > max_len:
        return v[: max_len - 1] + "…"
    else:
        return v


def filter_rows(data, expression):
    class RegexString(str):
        def __eq__(self, other):
            return bool(re.match("^" + other + "$", str(self)))

    scope = {}
    result = []
    for row in data:
        for key, value in row.items():
            if isinstance(value, str):
                scope[key] = RegexString(value)
            else:
                scope[key] = value
        if eval(expression, scope):
            result.append(row)

    return result


@click.command(name="error", cls=BaseCommandConfig, log_handlers=["file"])
@click.argument("set_name", metavar="<SET>", required=True)
@click.option(
    "--from",
    "-f",
    "time_from",
    metavar="<file>",
    cls=DateTimeOption,
    help="Start time (default: derived from --offset)",
)
@click.option("--to", "-t", "time_to", metavar="<file>", cls=DateTimeOption, help="End time (default: current time)")
@click.option("--offset", "-o", metavar="<int>", cls=OffsetOption, help="Time offset to derive --from from --to")
@click.option("--index", "-i", "use_index", is_flag=True, default=False, help="Create index for entries.")
@click.option(
    "--index-file", "indexfile", metavar="<file>", default=None, help="Use index file instead of the default one."
)
@click.option("--filter", "filter_expression", metavar="<expression>", required=False, help="filter expression.")
@click.option("--silent", "-s", "silent", is_flag=True, default=False, help="Do not display progress and other stats.")
def get_error(config, log, silent, set_name, time_from, time_to, offset, use_index, indexfile, filter_expression):
    if indexfile is not None and not use_index:
        raise Exception("The --index-file option can be used only with --index.")
    cleanup_indexdir()

    start_time = time.time()
    logs_set = config(f"sets.{set_name}")
    if logs_set is None:
        raise Exception(f"The log set '{set_name}' not found in the configuration file.")

    if time_from is None and time_to is None:
        raise Exception("Either --from or --to must be specified.")

    if time_to is None:
        time_to = datetime.now()

    if time_from is None and offset is not None:
        time_from = time_to - offset

    if not silent:
        print(f"-- Time range: {time_from} - {time_to}")

    ext_parser = load_parser(config("parsers"), [set_name])

    index = None
    if use_index:
        if not silent:
            print(f"-- The index will be created" + ("." if indexfile is None else f" in the file {indexfile}."))
        index = SOAGroupIndex(time_from, time_to, set_name, indexfile=indexfile)

    if not silent:
        print(f"-- Searching files in the set '{set_name}'")
    soa_files = get_files(
        logs_set.directories,
        time_from,
        time_to,
        lambda fname: re.search(logs_set.filename_pattern, fname),
    )

    if len(soa_files) == 0 and not silent:
        print("-- No files found.")
        return

    total_size = sum([item["end_pos"] - item["start_pos"] for items in soa_files.values() for item in items])
    num_files = sum([len(items) for items in soa_files.values()])
    pbar = (
        tqdm(
            desc=f"-- Reading entries from {num_files} files",
            total=total_size,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            ncols=100,
        )
        if not silent
        else None
    )

    def _read_entries(server, item):
        reader = SOALogReader(item["file"])
        reader.open()
        try:
            entries = reader.read_entries(start_pos=item["start_pos"], time_to=time_to, progress=pbar)
            for group in reader.group_entries(entries, time_to=time_to, ext_parser=ext_parser, index=index):
                d = group.to_dict()
                if sys.stdout.isatty():
                    d["composite"] = format_composite(d["composite"])
                d["file"] = item["file"]
                item["data"].append(d)
                item["data"][-1]["server"] = server
        finally:
            reader.close()

    for server, items in soa_files.items():
        for item in items:
            _read_entries(server, item)

    if use_index:
        index.write()

    pbar.close() if pbar is not None else None
    data = []
    for server, items in soa_files.items():
        for item in items:
            data.extend(filter_rows(item["data"], filter_expression) if filter_expression is not None else item["data"])

    data = sorted(data, key=lambda x: x["time"])

    if len(data) == 0 and not silent:
        print("-- No errors found.")
        return

    if not silent:
        print(f"-- Completed in {time.time() - start_time:.2f}s")

    table_def = [
        {"name": "TIME", "value": "{time}", "help": "Error time"},
        {"name": "SERVER", "value": "{server}", "help": "Server name"},
        {"name": "FLOW_ID", "value": "{flow_id}", "help": "Flow ID"},
        {"name": "COMPOSITE", "value": "{composite}", "help": "Composite name"},
    ]
    for key in ext_parser.keys():
        table_def.append({"name": key.upper(), "value": "{" + key + "}", "help": "Extended attribute"})
    if use_index:
        table_def.append({"name": "INDEX", "value": "{index}", "help": "Index entry ID"})

    Table(table_def, None, False).display(data)
    if not silent:
        print(f"-- Errors: {len(data)}")


@click.command(name="index", cls=BaseCommandConfig, log_handlers=["file"])
@click.argument("id", required=True)
@click.option("--stdout", "-s", is_flag=True, help="Print to stdout instead of using less")
@click.option("--index-file", "indexfile", default=None, help="Use index file instead of the default one.")
def index_error(config, log, id, stdout, indexfile):
    index = SOAGroupIndex(indexfile=indexfile)
    index.read()
    item = index.search(id)
    if item is None:
        raise Exception(f"Index entry '{id}' not found.")
    else:
        if not stdout:
            cmd = ["less"]
            subprocess.run(cmd, input=index.output(item))
        else:
            sys.stdout.write(index.output(item).decode("utf-8", errors="replace"))


@click.group(help="SOA log commands.")
def soa():
    pass


soa.add_command(get_error)
soa.add_command(range)
soa.add_command(index_error)
