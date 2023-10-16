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

from .log import log, filter_rows
from ..log import (
    OSBLogReader,
    LogReader,
    OutLogEntry,
    get_files,
    list_files,
    DEFAULT_DATETIME_FORMAT,
    EntryIndex,
    cleanup_indexdir,
    LabelParser,
)

from ..json2table import Table
from ..config import DATA_DIR

from .click_ext import BaseCommandConfig, DateTimeOption, OffsetOption


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

    label_parser = LabelParser(config("parsers"), [set_name])

    index = None
    if use_index:
        if not silent:
            print(f"-- The index will be created" + ("." if indexfile is None else f" in the file {indexfile}."))
        index = EntryIndex(time_from, time_to, set_name, indexfile=indexfile)

    if not silent:
        print(f"-- Searching files in the set '{set_name}'")
    osb_files = get_files(
        logs_set.directories,
        time_from,
        time_to,
        lambda fname: re.search(logs_set.filename_pattern, fname),
    )

    if len(osb_files) == 0 and not silent:
        print("-- No files found.")
        return

    total_size = sum([item["end_pos"] - item["start_pos"] for items in osb_files.values() for item in items])
    num_files = sum([len(items) for items in osb_files.values()])
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
        reader = OSBLogReader(item["file"])
        reader.open()
        try:
            entries = reader.read_entries(start_pos=item["start_pos"], time_to=time_to, progress=pbar)
            for entry in entries:
                if entry.type in ["Error", "Warning"]:
                    d = entry.to_dict(label_parser)
                    if use_index:
                        d["index"] = index.create_item(item["file"], [entry.message])
                    d["file"] = item["file"]
                    item["data"].append(d)
                    item["data"][-1]["server"] = server
        finally:
            reader.close()

    for server, items in osb_files.items():
        for item in items:
            _read_entries(server, item)

    if use_index:
        index.write()

    pbar.close() if pbar is not None else None
    data = []
    for server, items in osb_files.items():
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
    if use_index:
        table_def.append({"name": "INDEX", "value": "{index}", "help": "Index entry ID"})

    Table(table_def, None, False).display(data)
    if not silent:
        print(f"-- Errors: {len(data)}")


@click.group(help="OSB log commands.")
def osb():
    pass


log.add_command(osb)
osb.add_command(get_error)
