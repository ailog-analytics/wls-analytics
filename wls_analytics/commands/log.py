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
    EntryIndex,
    cleanup_indexdir,
)

from ..json2table import Table
from ..config import DATA_DIR

from .click_ext import BaseCommandConfig, DateTimeOption, OffsetOption


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


@click.command(name="range", cls=BaseCommandConfig, help="Display log time ranges.", log_handlers=["file"])
@click.argument("set_name", metavar="<SET>", required=True)
def get_range(config, log, set_name):
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


@click.command(name="index", cls=BaseCommandConfig, log_handlers=["file"], help="Read entries from log index.")
@click.argument("id", required=True)
@click.option("--stdout", "-s", is_flag=True, help="Print to stdout instead of using less")
@click.option("--index-file", "indexfile", default=None, help="Use index file instead of the default one.")
def index_error(config, log, id, stdout, indexfile):
    index = EntryIndex(indexfile=indexfile)
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


@click.group(help="Log commands.")
def log():
    pass


log.add_command(get_range)
log.add_command(index_error)
