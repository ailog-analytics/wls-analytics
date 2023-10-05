# -*- coding: utf-8 -*-
# @author: Tomas Vitvar, https://vitvar.com, tomas.vitvar@oracle.com

import re
import sys
import os
import collections
import json
import time
import signal

from io import StringIO


MAP_IGNORE_KEY_ERROR = True


class Map(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__set_data__(*args, **kwargs)

    def __set_data__(self, *args, **kwargs):
        for arg in args:
            if isinstance(arg, dict):
                for k, v in arg.items():
                    if isinstance(v, dict):
                        self[k] = Map(v)
                    else:
                        self[k] = v

        if kwargs:
            for k, v in kwargs.items():
                if isinstance(v, dict):
                    self[k] = Map(v)
                else:
                    self[k] = v

    def __getattr__(self, attr):
        a = self.get(attr)
        if a is None and not MAP_IGNORE_KEY_ERROR:
            raise KeyError(f'The key "{attr}" is undefined!')
        return a

    def __setattr__(self, key, value):
        self.__setitem__(key, value)

    def __delattr__(self, item):
        self.__delitem__(item)

    def __setitem__(self, key, value):
        super(Map, self).__setitem__(key, value)
        self.__dict__.update({key: value})

    def __delitem__(self, key):
        super(Map, self).__delitem__(key)
        del self.__dict__[key]

    def to_json(self, encoder=None, exclude=[]):
        d = {k: v for k, v in self.__dict__.items() if k not in exclude}
        return json.dumps(d, skipkeys=True, cls=encoder)

    def update(self, map):
        if isinstance(map, Map):
            self.__dict__.update(map.__dict__)
        if isinstance(map, dict):
            self.__dict__.update(map)

    def __setstate__(self, state):
        self.update(state)

    def search(self, callback, item=None, expand=None, data=None):
        if item == None:
            item = self
        if isinstance(item, dict):
            for k, v in item.items():
                if not expand or expand(k):
                    data = self.search(callback, v, expand, callback(k, v, data))
        if isinstance(item, list):
            for v in item:
                data = self.search(callback, v, expand, data)
        return data


class PathDef:
    def __init__(self, path_def):
        self.path_def = path_def

    def params(self, path):
        path_re = self.path_def

        # find all params in path_def
        params_def = re.findall("(\{[a-zA-Z0-9_\.]+\})", self.path_def)

        # create re pattern by replacing parameters in path_def with pattern to match parameter values
        for p_def in params_def:
            path_re = path_re.replace(p_def, "([a-zA-Z\-0-9\._]+)")

        # get params values
        res = re.findall("^" + path_re + "$", path)
        values = []
        for x in res:
            if type(x) is tuple:
                values.extend(list(x))
            else:
                values.append(x)

        params = Map()
        params.params = Map()
        params.__path_def__ = self.path_def
        params.__path__ = path
        params.replace = self.replace
        for x in range(0, len(params_def)):
            if x < len(values):
                params.params[params_def[x][1:-1]] = str(values[x])
            else:
                # Msg.warn_msg("The path '%s' does not match definition '%s'"%(path, self.path_def))
                return None

        return params

    def replace(self, params, paramsMap):
        new_path = params.__path__
        for k, v in paramsMap.items():
            if params.params.get(k):
                new_path = new_path.replace("%s" % params.params.get(k), v, 1)
            else:
                raise Exception("The param '%s' has not been found in path definition '%s'." % (k, self.path_def))

        return new_path


def remove_ansi_escape(text):
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


class Table:
    def __init__(self, table_def, sort_cols, sort_reverse):
        self.table_def = table_def
        self.sort_def = self._sort_def(sort_cols, table_def)
        self.sort_reverse = sort_reverse

    def _sort_def(self, sort_cols, table_def):
        if sort_cols is None:
            return None
        sort_def = collections.OrderedDict.fromkeys([s.strip().upper() for s in sort_cols.split(",")])
        for e in self.table_def:
            if e.get("value"):
                params = PathDef(e.get("value")).params(e["name"])
                for s in sort_def.keys():
                    for k, v in params.params.items():
                        if v.upper() == s:
                            sort_def[s] = "{%s}" % k
                            break
        return sort_def

    def format_item(self, cdef, value, skipformat=False, entry=None, adjust=True, global_format=None):
        if cdef.get("format") and not skipformat:  # and value is not None:
            try:
                v = str(cdef["format"](cdef, value, entry))
            except:
                v = "E!"
        else:
            v = str(value) if value is not None else "-"

        if global_format:
            v = global_format(cdef, v, entry)

        asize = 0
        if adjust and cdef.get("_len"):
            asize = cdef["_len"] + 2
        if cdef.get("mlen") and len(remove_ansi_escape(v)) > cdef["mlen"]:
            v = v[: cdef["mlen"] - 1] + "…"

        if not cdef.get("justify") or cdef.get("justify") == "left":
            return f"{v}" + "".join([" " for x in range(asize - len(remove_ansi_escape(v)))])
        if cdef.get("justify") == "right":
            return "".join([" " for x in range(asize - len(remove_ansi_escape(v)))]) + f"{v}"

    def get_field(self, field_name, data):
        d = data
        for f in field_name.split("."):
            try:
                d = d.get(f)
            except:
                return None
        return d

    def eval_value(self, value, data):
        if not value:
            return None
        # get fields placeholders: {placeholder}
        params = list(set(re.findall("\{[a-zA-Z0-9_\-\.]+\}", value)))
        val = value
        if len(params) > 1:
            for k in params:
                val = val.replace(k, str(self.get_field(k[1:-1], data)))
            return val
        if len(params) == 1:
            return self.get_field(params[0][1:-1], data)
        if len(params) == 0:
            return value

    def calc_col_sizes(self):
        for cdef in self.table_def:
            l = len(self.format_item(cdef, cdef["name"], skipformat=True, entry=None, adjust=False))
            if cdef.get("_len") is None or l > cdef["_len"]:
                if cdef.get("mlen") is not None and l > cdef["mlen"]:
                    l = cdef["mlen"]
                cdef["_len"] = l

        for e in self.data:
            for cdef in self.table_def:
                l = len(
                    remove_ansi_escape(
                        self.format_item(
                            cdef,
                            self.eval_value(cdef.get("value"), e),
                            skipformat=False,
                            entry=e,
                            adjust=False,
                        )
                    )
                )
                if cdef.get("_len") is None or l > cdef["_len"]:
                    if cdef.get("mlen") is not None and l > cdef["mlen"]:
                        l = cdef["mlen"]
                    cdef["_len"] = l

    def getTerminalCols(self):
        cols = 1000
        try:
            cols = int(os.popen("stty size", "r").read().split()[1])
        except Exception as e:
            sys.stderr.write("Cannot determine terminal dimensions: %s/n" % (str(e)))
            pass
        return cols

    def display(self, data, noterm=False, global_format=None, format=None, csv_delim=";"):
        if format is not None and format not in ["json", "csv"]:
            raise Exception("Invalid format value {format}. The allowed values are 'csv' or 'json'.")

        # sort data
        if self.sort_def is not None:
            data = sorted(
                data,
                key=lambda item: tuple(self.eval_value(v, item) for k, v in self.sort_def.items() if v is not None),
                reverse=self.sort_reverse,
            )

        # calc
        self.data = data
        self.calc_col_sizes()

        # display header
        lines = []
        line = []
        delim = csv_delim if format is not None else ""
        for cdef in self.table_def:
            if format is None:
                line.append(
                    self.format_item(
                        cdef,
                        cdef["name"],
                        skipformat=True,
                        entry=None,
                        adjust=not (noterm),
                    )
                )
            else:
                line.append('"' + cdef["name"] + '"')
        lines.append(delim.join(line))

        def _wrap_str(val):
            if isinstance(val, str):
                return f'"{val}"'
            if isinstance(val, list):
                return ",".join(val)
            return str(val)

        # display rows
        for e in self.data:
            line = []
            for cdef in self.table_def:
                if format is None:
                    line.append(
                        self.format_item(
                            cdef,
                            self.eval_value(cdef.get("value"), e),
                            skipformat=False,
                            entry=e,
                            adjust=not (noterm),
                            global_format=global_format,
                        )
                    )
                else:
                    line.append(_wrap_str(self.eval_value(cdef.get("value"), e)))
            lines.append(delim.join(line))

        if not (noterm) and format is None and sys.stdout.isatty():
            cols = self.getTerminalCols()
        else:
            cols = 100000

        if format is None or format == "csv":
            for line in lines:
                sys.stdout.write("%s\n" % line[0:cols])
        else:
            header = None
            data = []
            for line in lines:
                items = [x.replace('"', "") for x in line.split(";")]
                if header is None:
                    header = items
                else:
                    row = {}
                    for inx, value in enumerate(items):
                        row[header[inx]] = value
                    data.append(row)
            print(json.dumps(data, indent=4, sort_keys=True, default=str))
        return len(lines)

    def watch(self, data_func, refresh_interval=1, hide_cursor=True):
        """
        Displays the data returned by the data_func function in a loop with the refresh_interval interval.
        The data_func function must return a list of dicts. The dicts must have keys that correspond to the
        names of the columns in the table_def. The function can return None to indicate that there is no data
        to display in which case the function ends.
        """

        def _kill():
            if sys.stdout.isatty():
                bb = "\b\b"
            else:
                bb = ""
            sys.stdout.write(f"{bb}The process was interrupted.\n")
            sys.stdout.write(f"{bb}")
            if sys.stdout.isatty():
                sys.stdout.write("\033[?25h")
            sys.stdout.flush()
            exit(0)

        # kill the process when ctrl+c is pressed
        # since there may be threads that wait on i/o this is the only way to kill the process
        signal.signal(signal.SIGINT, lambda sig, frame: _kill())
        if hide_cursor and sys.stdout.isatty():
            sys.stdout.write("\033[?25l")
        try:
            data = None
            extra_lines = 0
            while True:
                original_stdout = sys.stdout
                capture_stream = StringIO()
                sys.stdout = capture_stream
                try:
                    _data = data_func()
                    _lines = capture_stream.getvalue()
                    extra_lines = len(_lines.split("\n")) - 1 if _lines != "" else 0
                finally:
                    sys.stdout = original_stdout

                if data is not None:
                    if sys.stdout.isatty():
                        print("".join(["\033[A" for i in range(len(data) + 2 + extra_lines)]))
                    else:
                        print("---")

                if _data is not None:
                    if _lines != "":
                        sys.stdout.write(_lines)
                    self.display(_data)
                    time.sleep(refresh_interval)
                    data = _data
                else:
                    break
            if data is not None:
                self.display(data)
        except KeyboardInterrupt:
            pass
        finally:
            if hide_cursor and sys.stdout.isatty():
                sys.stdout.write("\033[?25h")
                sys.stdout.flush()
            signal.signal(signal.SIGINT, signal.SIG_DFL)

    def describe(self, noterm=False):
        mlen = 0
        for cdef in self.table_def:
            if cdef.get("name") is not None and len(cdef["name"]) > mlen:
                mlen = len(cdef["name"])

        if not (noterm):
            cols = self.getTerminalCols()
        else:
            cols = 1000

        for cdef in self.table_def:
            if cdef.get("name") is not None:
                line = "{name}  {descr}\n".format(name=cdef["name"].ljust(mlen), descr=cdef.get("help", "n/a"))
                sys.stdout.write(line[0:cols])
