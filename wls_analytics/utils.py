# -*- coding: utf-8 -*-
# @author: Tomas Vitvar, https://vitvar.com, tomas@vitvar.com

import random
import string
import re
import time
import json
import ast
import itertools
from collections.abc import Iterable

from functools import reduce


class bcolors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKGREEN = "\033[32m"
    WARNING = "\033[33m"
    ERROR = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    LIGHTGRAY = "\033[90m"
    MAGENTA = "\033[35m"


def format_str_color(str, color, disable=False):
    color = None if disable else color
    return (color if color is not None else "") + str + (bcolors.ENDC if color is not None else "")


class PythonExpression:
    def __init__(self, expr):
        self.expr_str = expr
        self.expr = self.compile()

    def compile(self):
        return compile(self.expr_str, "<string>", "eval")

    def eval(self, scope):
        return eval(self.expr, scope, scope)

    def __getstate__(self):
        return (self.expr_str, None)

    def __setstate__(self, state):
        self.expr_str, _ = state
        self.expr = self.compile()

    def ast(self):
        parsed_code = ast.parse(self.expr_str)
        return parsed_code

    def __str__(self):
        return "!py %s" % self.expr_str

    def __repr__(self) -> str:
        return self.__str__()


MAP_IGNORE_KEY_ERROR = True


# *** helper Map object
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


def deep_eval(data, scope, log=None, raise_ex=False):
    if isinstance(data, dict):
        for key, value in data.items():
            data[key] = deep_eval(value, scope, log, raise_ex)
    elif isinstance(data, list):
        for inx, x in enumerate(data):
            data[inx] = deep_eval(x, scope, log, raise_ex)
    elif callable(getattr(data, "eval", None)):
        try:
            data = data.eval(scope)
        except Exception as e:
            if log is not None:
                log.error(f"The Python expression '{data.expr_str}' failed. %s." % (str(e)))
            if raise_ex:
                raise
            else:
                data = None
    return data


def deep_find(dic, keys, default=None, type=None, delim="."):
    val = reduce(
        lambda di, key: di.get(key, default) if isinstance(di, dict) else default,
        keys.split(delim),
        dic,
    )
    if val == default:
        return default
    return type(val) if type != None else val


def import_class(name):
    components = name.split(".")
    mod = __import__(components[0])
    for comp in components[1:]:
        mod = getattr(mod, comp)
    return mod


def randomString(stringLength=10):
    """Generate a random string of fixed length"""
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(stringLength))


def is_number(s):
    s = str(s)
    p = re.compile(r"^[\+\-]?[0-9]*(\.[0-9]+)?$")
    return s != "" and p.match(s)


def deep_merge(source, destination):
    for key, value in source.items():
        if isinstance(value, dict):
            node = destination.setdefault(key, {})
            deep_merge(value, node)
        else:
            if key in destination and isinstance(destination[key], list) and isinstance(value, list):
                for x in value:
                    destination[key].append(x)
            else:
                if key not in destination:
                    destination[key] = value
    return destination


def merge_dicts(*dicts):
    result = {}
    for d in dicts:
        if d is not None:
            result.update(d)
    return result


def remove_ansi_escape(text):
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def str2bool(s):
    if type(s) == str:
        return s.lower() in ["True", "true"]
    else:
        raise Exception(f"Invalid type: {type(s)}")


def merge(dict1, dict2):
    if not dict1:
        dict1 = {}
    z = dict1.copy()
    z.update(dict2)
    return z


class IndexWordGenerator(Iterable):
    def __init__(self) -> None:
        def randomize(s):
            _s = list(s)
            random.shuffle(_s)
            return "".join(_s)

        vowels = "aeiou"
        consonants = "bcdfghjklmnprstvwxz"
        numbers = "123456789"
        self.words = list(
            itertools.product(
                randomize(consonants),
                randomize(vowels),
                randomize(consonants),
                randomize(vowels),
                randomize(numbers),
                randomize(numbers + "0"),
            )
        )
        self.current_set = None
        self.size = 200000
        self.next_set(0, self.size)

    def next_set(self, start, end):
        if start >= len(self.words):
            raise False
        self.index = 0
        self.current_set = self.words[start : min(end, len(self.words))]
        random.shuffle(self.current_set)
        self.start = start
        self.end = end
        return True

    def __iter__(self):
        return self

    def __next__(self):
        if self.index >= len(self.current_set):
            if not self.next_set(self.end + 1, self.end + self.size):
                raise StopIteration
        value = self.current_set[self.index]
        self.index += 1
        return "".join(value)
