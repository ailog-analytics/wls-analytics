# -*- coding: utf-8 -*-
# @author: Tomas Vitvar, https://vitvar.com, tomas.vitvar@oracle.com

import click
from datetime import datetime, timedelta
from tqdm import tqdm
import re
import time
import sys

from ..log import (
    get_files,
    EntryIndex,
    cleanup_indexdir,
    LabelParser,
)

from ..json2table import Table
from .click_ext import BaseCommandConfig, DateTimeOption, OffsetOption, get_set_reader, get_time_range


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


@click.command(name="out", cls=BaseCommandConfig, help="Display entries from out log files.", log_handlers=["file"])
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
@click.option("--types", default=None, help="Entry types (default: Error).")
def out_log(config, log, silent, set_name, time_from, time_to, offset, use_index, indexfile, filter_expression, types):
    if indexfile is not None and not use_index:
        raise Exception("The --index-file option can be used only with --index.")

    entry_types = [x.upper() for x in types.split(",")] if types is not None else ["ERROR"]
    logs_set, reader_class = get_set_reader(config, set_name)

    time_from, time_to = get_time_range(time_from, time_to, offset)
    if not silent:
        print(f"-- Time range: {time_from} - {time_to}")

    cleanup_indexdir()
    label_parser = LabelParser(config("parsers"), [set_name])

    index = None
    if use_index:
        if not silent:
            print(f"-- The index will be created" + ("." if indexfile is None else f" in the file {indexfile}."))
        index = EntryIndex(time_from, time_to, set_name, indexfile=indexfile)

    if not silent:
        print(f"-- Searching files in the set '{set_name}'")

    start_time = time.time()
    out_files = get_files(
        reader_class,
        logs_set.directories,
        time_from,
        time_to,
        lambda fname: re.search(logs_set.filename_pattern, fname),
    )

    if len(out_files) == 0 and not silent:
        print("-- No log files found.")
        return

    total_size = sum([item["end_pos"] - item["start_pos"] for items in out_files.values() for item in items])
    num_files = sum([len(items) for items in out_files.values()])
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
        reader = reader_class(item["file"])
        reader.open()
        try:
            for entry in reader.read_entries(start_pos=item["start_pos"], time_to=time_to, progress=pbar):
                if entry.type.upper() in entry_types:
                    d = entry.to_dict(label_parser)
                    if use_index:
                        d["index"] = index.create_item(item["file"], entry.message)
                    d["file"] = item["file"]
                    item["data"].append(d)
                    item["data"][-1]["server"] = server
        finally:
            reader.close()

    for server, items in out_files.items():
        for item in items:
            _read_entries(server, item)

    if use_index:
        index.write()

    pbar.close() if pbar is not None else None
    data = []
    for server, items in out_files.items():
        for item in items:
            data.extend(filter_rows(item["data"], filter_expression) if filter_expression is not None else item["data"])

    data = sorted(data, key=lambda x: x["time"])

    if len(data) == 0 and not silent:
        print("-- No errors found.")
        return

    if not silent:
        print(f"-- Completed in {time.time() - start_time:.2f}s")

    Table(reader_class(None).get_tabledef(label_parser, use_index), None, False).display(data)
    if not silent:
        print(f"-- Errors: {len(data)}")
