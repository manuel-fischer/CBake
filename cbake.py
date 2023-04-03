#!/usr/bin/python
from functools import reduce
import os
import sys
import struct
from sys import stderr

from multiprocessing.pool import ThreadPool
from threading import Lock

# TODO relative inclusion of header files in include/ from src/ not supported by vscode

import json
from dataclasses import dataclass
import time
from typing import Callable, Dict, List, Tuple

CBAKE_DEP_FILE = ".cbake-dependencies.txt"
CBAKE_DEP_FILE_DBG = ".cbake-dependencies-dbg.txt"

def pjoin(*paths):

    path = []

    for p in paths:
        if os.sep != "/": p = p.replace("/", os.sep)
        for d in p.split("/"):
            if not d:
                pass
            elif d == ".":
                pass
            elif d == "..":
                try:
                    path.pop()
                except IndexError:
                    raise FileNotFoundError
            else:
                path.append(d)

    return os.sep.join(path)

def eprint(*args, end='\n', file=sys.stderr):
    print(*args, end=end, file=file)

class LazyPrinter:
    content : str
    print : Callable

    def __init__(self, print=eprint):
        self.content = ""
        self.print = print

    def __call__(self, *args, sep=None, end='\n'):
        if sep is None: sep = ' '
        self.content += sep.join(args) + end

    def print_all(self):
        self.print(self.content, end="")


CC = "gcc"
CXX = "g++"


FILE_NOT_FOUND = object()
FILE_AMBIGUOUS = object()


def system_flags():
    return {
        "WIN": os.name == 'nt',
        str(struct.calcsize("P") * 8): True # Pointer bit width: 32 for 32-bit; 64 for 64-bit eg x64
    }

class CBakeCtx:
    cbake_dep_file : str
    flags : Dict[str, bool]
    out_prefix : str
    path_cache : Dict[str, str]

    # set separately, json object
    settings : object

    def __init__(self):
        self.cbake_dep_file = CBAKE_DEP_FILE

        # used by the special syntax:
        #  `@!WIN&64: -opt`, this enables `-opt` only if the current platform
        #  is not Windows and the current platform is a 64 bit system
        self.flags = system_flags()

        # prefix to all output files
        self.out_prefix = ""

        # filename -> effective_path | FILE_NOT_FOUND | FILE_AMBIGUOUS
        # None if the file is cannot be found
        self.path_cache = {}

class ParallelWorkerCtx:
    _thread_pool : ThreadPool
    _stop : bool
    _lock : Lock

    def __init__(self, thread_count):
        self._thread_pool = ThreadPool(thread_count)
        self._stop = False
        self._lock = Lock()

    def raise_stop(self):
        with self._lock:
            # Finish pending tasks, the following would cancel them:
            #if not self._stop:
            #    self._thread_pool.terminate()
            self._stop = True

    def is_stop_raised(self):
        with self._lock:
            return self._stop

    def execute(self, func, tasks):
        def yield_func():
            for t in tasks:
                if self.is_stop_raised(): return
                yield t

        return self._thread_pool.imap_unordered(func, yield_func())



@dataclass
class CompilationResult:
    filename : str
    exit_code : int
    elapsed_time : float # seconds
    warnings : int
    errors : int

    @property
    def success(self):
        return self.exit_code == 0


def pretty_format_csv(attrs : List[Tuple[str,str,str,str]], l : List[object]):
    attr_names = [attr_csv for attr, attr_csv, attr_fmt, align in attrs]
    cells = [
        [attr_fmt.format(getattr(row, attr)) for attr, attr_csv, attr_fmt, align in attrs]
        for row in l
    ]
    all_cells = [attr_names] + cells

    all_cell_len = ((len(cell) for cell in row) for row in all_cells)

    hd_max = lambda a, b: map(lambda aibi: max(*aibi), zip(a, b))

    widths = tuple(reduce(
        hd_max,
        all_cell_len,
        (0,) * len(attrs)
    ))


    format_str = " ; ".join(
        f"{{:{align}{w}}}"
        for w, (attr, attr_csv, attr_fmt, align) in zip(widths, attrs)
    )


    return "\n".join(format_str.format(*row).rstrip() for row in all_cells)

    




# Files could be moved between src/ and include/!
# this needs to be handled correctly
#
# files with the same name in the src/ and include/
# directories are not allowed
def get_effective_path_(path):
    global has_invalid_includes

    src_path = pjoin("src", path)
    inc_path = pjoin("include", path)

    in_src     = os.path.exists(src_path)
    in_include = os.path.exists(inc_path)

    if in_src and in_include and src_path != inc_path:
        return FILE_AMBIGUOUS

    if in_src:     return src_path
    if in_include: return inc_path

    return FILE_NOT_FOUND

def get_effective_path_s(ctx : CBakeCtx, path):
    try:    return ctx.path_cache[path]
    except: pass

    epath = get_effective_path_(path)
    ctx.path_cache[path] = epath
    return epath

def get_effective_path(ctx : CBakeCtx, path):
    epath = get_effective_path_s(ctx, path)
    if epath == FILE_NOT_FOUND: raise FileNotFoundError
    if epath == FILE_AMBIGUOUS: raise Exception("Ambiguous Filename")
    return epath

def dbg(locals_dict):
    for k, v in locals_dict.items():
        eprint(f"{k+':':20} {v}")


def conditional_element(ctx : CBakeCtx, s: str) -> str:
    if s.startswith('@'):
        s = s[1:]

        flag_end = s.find(':')
        flag_list = s[:flag_end].split('&')

        for flag in flag_list:
            flag = flag.strip()
            negated = flag.startswith('!')
            if negated: flag = flag[1:].strip()

            result = flag in ctx.flags and ctx.flags[flag]
            if negated: result = not result
            if not result:
                return ""

        return s[flag_end+1:]
    else:
        return s


def collect_args(ctx : CBakeCtx, str_or_list) -> str:
    if type(str_or_list) == str: return str_or_list
    else: return " ".join(map(lambda s: conditional_element(ctx, s), str_or_list))


def read_dep_file(ctx : CBakeCtx):
    file_times = {}
    file_includes = {}
    try:
        with open(ctx.cbake_dep_file) as f:
            for l in f.readlines():
                l = l.strip()

                if not l: continue

                fn, time, *includes = map(str.strip, l.split())
                time = float(time)
                file_times[fn] = time
                def parse_include(inc):
                    at_pos = inc.rfind('@')
                    ln = int(inc[at_pos+1:])
                    return inc[:at_pos], ln
                file_includes[fn] = list(map(parse_include, includes))
                                  # [parse_include(inc) for inc in includes]
    except FileNotFoundError: pass

    return file_times, file_includes


def write_dep_file(ctx : CBakeCtx, file_times, file_includes):

    files = sorted(file_times.keys())
    with open(ctx.cbake_dep_file, "w") as f:
        for fn in files:
            s_time = str(file_times[fn])
            s_includes = " ".join(f"{fname}@{ln}" for fname, ln in file_includes[fn])
            print(f"{fn} {s_time} {s_includes}", file = f)


def get_err_msg(efn):
    if efn == FILE_NOT_FOUND:
        return "No such file or directory"
    if efn == FILE_AMBIGUOUS:
        return "Ambiguous file include"


def get_includes(filename, efilename):
    with open(efilename) as f:
        for ln, rl in enumerate(f.readlines()):
            l = rl.strip()

            if not l: continue
            if l[0] != '#': continue

            l = l[1:].strip()

            if not l.startswith("include"): continue

            l = l[len("include"):].strip()

            if not l[0] == '"': continue

            l = l[1:]
            end = l.find('"')
            fname = l[:end]
            rfname = fname

            if os.sep != "/":
                fname = fname.replace("/", os.sep)

            if fname.startswith("."):
                fname = os.path.split(filename)[0] + "/" + fname

            #fnd = rl.find(rfname)
            #if msg := get_err_msg(efn):
            #    herefile = efilename # pjoin(os.getcwd(), efilename)
            #    eprint(
            #        f"{herefile}:{ln+1}:{fnd+1-1}: fatal error: {rfname}: {msg}\n" +
            #        f" {rl.rstrip()}\n" +
            #        " "*(fnd+1-1) + "^" + "~"*(2-1+len(rfname)),
            #        end = "\n\n"
            #    )

            yield fname, ln+1


def check_includes(ctx, filename, efilename, includes):
    contents = None
    success = True
    for fname, ln in includes:
        efn = get_effective_path_s(ctx, fname)
        if msg := get_err_msg(efn):
            if contents is None:
                with open(efilename) as f:
                    contents = f.readlines()

            success = False

            l = contents[ln-1]
            a = l.find('"')
            b = l.find('"', a+1)
            rfname = l[a+1:b]

            herefile = efilename # pjoin(os.getcwd(), efilename)
            eprint(
                f"{herefile}:{ln}:{a+1-1}: fatal error: {rfname}: {msg}\n" +
                f" {l.rstrip()}\n" +
                " "*(a+1-1) + "^" + "~"*(2-1+len(rfname)),
                end = "\n\n"
            )

    return success


def get_included_files(includes):
    return set(fname for fname, location in includes)

def collect_files(path):
    lst = os.listdir(path)
    for fn in lst:
        p = pjoin(path, fn)
        if os.path.isdir(p):
            yield from collect_files(p)
        else:
            yield p

def collect_sources():
    for f in collect_files("src"):
        if os.path.splitext(f)[1] in [".c", ".cpp"]:
            assert f[0:4] == "src" + os.sep
            yield f[4:]

def discover(ctx : CBakeCtx, file_times, file_includes, sources):
    success = True


    # rebuilding: automatically removing unreferenced files
    new_file_times = {}
    new_file_includes = {}



    known_files = set(file_times.keys())

    src_files = set(sources)
    cur_files = set(src_files) # copy
    checked_files = set()

    modified_files = set()

    # forward pass: find included files
    while cur_files:
        next_files = set()
        checked_files |= cur_files

        for fn in cur_files:
            efn = get_effective_path_s(ctx, fn)
            assert get_err_msg(efn) == None

            f_time = os.path.getmtime(efn)

            if fn not in known_files or \
               f_time > file_times[fn]:

                #print(f"{fn} modified")

                includes = list(get_includes(fn, efn))
                modified_files |= {fn}
            else:
                includes = file_includes[fn]

            if not check_includes(ctx, fn, efn, includes):
                success = False

            else:

                included_files = get_included_files(includes)
                next_files |= included_files - checked_files
                new_file_includes[fn] = includes

                new_file_times[fn] = f_time
                known_files |= {fn}


        cur_files = next_files


    # middle pass: create backpointers, invert graph
    included_from = {}
    for fn, includes in new_file_includes.items():
        for ff, lineno in includes:
            #if ff in modified_files:
            #    print(f"{fn} includes {ff}")
            if ff in included_from:
                included_from[ff] |= {fn}
            else:
                included_from[ff] = {fn}


    # backward pass: propagate modifications
    recompile = set()

    cur_files = modified_files
    propagated_files = set()

    while cur_files:
        next_files = set()
        propagated_files |= cur_files

        for fn in cur_files:
            if fn in src_files:
                recompile |= {fn}

            if fn in included_from: # there are files including this file
                next_files |= included_from[fn] - propagated_files

        cur_files = next_files


    #dbg(locals())


    return new_file_times, new_file_includes, recompile, success




# behaves like os.system, but improves output messages for vscode
def exec_compiler(tprint, cmd) -> Tuple[int, int, int]: # exit_code, errors, warnings
    #cmd = " ".join(cmd)
    #return os.system(cmd)

    def esc(cmds):
        return f"\x1b[{cmds}"

    from subprocess import Popen, PIPE
    import re

    proc = Popen(cmd, stderr=PIPE)

    def colorize(l):
        l = l.replace("error:", esc('91m')+"error:"+esc('0m'))
        l = l.replace("warning:", esc('95m')+"warning:"+esc('0m'))
        l = l.replace("note:", esc('96m')+"note:"+esc('0m'))
        return l

    regexpr_msg  = re.compile("^(.*):(\\d+):(\\d+):\\s+(error|warning):\\s+(.*)$")
    regexpr_note = re.compile("^(.*):(\\d+):(\\d+):\\s+(note):\\s+(.*)$")
    re_file = 1
    re_line = 2
    re_colm = 3
    re_sevr = 4
    re_what = 5

    cur_file = None
    cur_line = None
    cur_colm = None
    cur_sevr = None
    cur_what = None

    def print_prev():
        if cur_file is None: return
        tprint(colorize(f"SUM: {cur_file}:{cur_line}:{cur_colm}: {cur_sevr}: {cur_what}"))
        tprint()
        tprint()

    errors = warnings = 0
    while True:
        l = proc.stderr.readline().decode()
        if l == "":
            returncode = proc.poll()
            if returncode is not None:
                print_prev()
                return (returncode, errors, warnings)
        else:
            if (m := re.match(regexpr_msg, l)) is not None:
                print_prev()
                cur_file = m.group(re_file)
                cur_line = m.group(re_line)
                cur_colm = m.group(re_colm)
                cur_sevr = m.group(re_sevr)
                cur_what = m.group(re_what)

                if cur_sevr == "error": errors += 1
                if cur_sevr == "warning": warnings += 1

            if (m := re.match(regexpr_note, l)) is not None:
                if "in expansion" in m.group(re_what):
                    cur_file = m.group(re_file)
                    cur_line = m.group(re_line)
                    cur_colm = m.group(re_colm)
                    # keep cur_sevr and cur_what

            tprint(colorize(l), end="")




def compile_object_file(tprint, ctx : CBakeCtx, fn) -> CompilationResult:
    fnn, ext = os.path.splitext(fn)
    if ext == ".c":
        comp_flags = collect_args(ctx, ctx.settings.get("c-flags", ""))
        comp  = CC
    else:
        comp_flags = collect_args(ctx, ctx.settings.get("cxx-flags", ""))
        comp  = CXX

    ofn = f"obj/{ctx.out_prefix + fnn}.o"

    # OK to be multithreaded, syscalls are synchronized
    os.makedirs(os.path.split(ofn)[0], exist_ok=True)

    # add include path relative to the include directory with the same name
    include_path_rel = "-I" + pjoin("include", os.path.split(fn)[0])
    include_path     = "-Iinclude"
    include_paths = [include_path] if include_path_rel == include_path else [include_path_rel, include_path]

    # TODO remove split:
    comp_flags = comp_flags.split()

    cmd = [comp, '-c', '-o', ofn, *include_paths, f'src/{fn}', *comp_flags]

    tprint(' '.join(cmd))
    start = time.time()
    exit_code, errors, warnings = exec_compiler(tprint, cmd)
    elapsed = time.time() - start

    return CompilationResult(
        filename=ofn,
        exit_code=exit_code,
        errors=errors,
        warnings=warnings,
        elapsed_time=elapsed
    )

def link_executable(tprint, ctx : CBakeCtx, sources) -> CompilationResult:
    has_cxx = False
    object_files = []
    for fn in sources:
        fnn, ext = os.path.splitext(fn)
        if ext == ".cpp": has_cxx = True
        object_files.append(f"obj/{ctx.out_prefix + fnn}.o")

    comp_flags = collect_args(ctx, ctx.settings.get("linker-flags", ""))

    if has_cxx: comp = CXX
    else:       comp = CC

    ofn = ctx.settings.get("program", "a.out")

    # TODO remove split:
    comp_flags = comp_flags.split()
    cmd = [comp, '-o', ctx.out_prefix + ofn, *object_files, *comp_flags]

    tprint(' '.join(cmd))
    start = time.time()
    exit_code, errors, warnings = exec_compiler(tprint, cmd)
    elapsed = time.time() - start

    return CompilationResult(
        filename=ofn,
        exit_code=exit_code,
        errors=errors,
        warnings=warnings,
        elapsed_time=elapsed
    )



def load_settings():
    with open("bakefile.json") as f:
        return json.loads(f.read())


def program_filename(ctx : CBakeCtx, name):
    n, ext = os.path.splitext(name)
    if ext == ".exe": return name
    if ctx.flags["WIN"]: # Windows
        return name + ".exe"
    return name


def process_files(ctx : CBakeCtx):
    # 1. discover
    # 2. compile
    # 3. update dependency file

    # 1. discover
    eprint("CBake: File discovery...")
    sources = list(collect_sources())
    file_times, file_includes = read_dep_file(ctx)
    n_file_times, n_file_includes, recompile, success = \
                  discover(ctx, file_times, file_includes, sources)

    if not success:
        eprint("CBake: File discovery failed")
        return False

    # 2. compile
    eprint("CBake: Object file compilation...")
    compilation_stats : List[CompilationResult] = []

    recompiled = set()
    threads = ctx.settings.get("threads", os.cpu_count() or 1)
    assert type(threads) == int
    assert threads >= 1
    if threads == 1: # single thread build
        for fn in recompile:
            result = compile_object_file(eprint, ctx, fn)
            compilation_stats.append(result)
            if not result.success:
                success = False
                break

            recompiled.add(fn)
    else:
        pctx = ParallelWorkerCtx(threads)

        def handle_task(fn):
            tprint = LazyPrinter()
            result = compile_object_file(tprint, ctx, fn)
            return (fn, tprint, result)

        for (fn, tprint, result) in pctx.execute(handle_task, recompile):
            tprint.print_all()
            compilation_stats.append(result)
            if not result.success:
                success = False
                pctx.raise_stop()
                continue # still handle other outputs

            recompiled.add(fn)


    # remove not compiled files from the list to invalidate
    not_compiled = recompile - recompiled
    for fn in not_compiled:
        del n_file_times[fn]
        del n_file_includes[fn]


    if success and recompile:
        eprint("CBake: Executable linking...")
        result = link_executable(eprint, ctx, sources)
        compilation_stats.append(result)
        success = result.success
    elif success:
            eprint("CBake: Nothing needs to be done")

    if not success:
        eprint("CBake: Compilation failed")

    # 3. update dependency
    if n_file_times != file_times or n_file_includes != file_includes:
        write_dep_file(ctx, n_file_times, n_file_includes)

    # 4. write statistics
    if (build_stats_file := ctx.settings.get("build-stats-file", None)) is not None:
        with open(build_stats_file, "wt") as f:
            compilation_stats.sort(key=lambda st: st.elapsed_time, reverse=True)
            f.write(pretty_format_csv(
                [
                    ("filename",     "filename", "{}",     ""),
                    ("exit_code",    "$?",       "{}",     ">"),
                    ("errors",       "E",        "{}",     ">"),
                    ("warnings",     "W",        "{}",     ">"),
                    ("elapsed_time", "T",        "{:.3f}", ">")
                ],
                compilation_stats
            ))

    return success


def remove(filename):
        try: os.remove(filename)
        except FileNotFoundError: pass


def print_help():
    print("cbake.py help | [clean build] [debug] [test] | [clean]")
    print("         (1)  |              (2)             |   (3)")
    print()
    print("The order of the arguments can be altered")
    print()
    print("   help         Shows this help")
    print("   clean/clear  Delete the executable and the dependency cache")
    print("   debug        Enables the debugging target")
    print("                The program filename is prefixed with 'dbg-'")
    print("                It sets the DEBUG flag to true")
    print("   build        Build, default, only required when used")
    print("                in combination with clean/clear")
    print("   test         Run the program after compilation")


@dataclass
class CmdFlags:
    debug : bool
    clean : bool
    build : bool
    test : bool

def parse_cmd_args(argv : List[str]) -> CmdFlags:
    if "help" in argv:
        print_help()
        return None

    cmd_flags = argv[1:]
    def pop_cmd_flag(name):
        nonlocal cmd_flags
        if name in cmd_flags:
            cmd_flags.remove(name)
            return True
        return False

    fs = CmdFlags(
        debug = pop_cmd_flag("debug"),
        clean = pop_cmd_flag("clean") or pop_cmd_flag("clear"),
        build = pop_cmd_flag("build"),
        test  = pop_cmd_flag("test"),
    )

    if cmd_flags:
        print(f"CBake: Warning: Ignored arguments:", file = stderr)
        print(f"    {' '.join(cmd_flags)}", file = stderr)

    return fs


def main(argv):
    from sys import argv, stderr

    fs = parse_cmd_args(argv)
    if not fs:
        return 2


    ctx = CBakeCtx()
    ctx.settings = load_settings()


    if fs.clean and not fs.build:
        if fs.debug or fs.build or fs.test:
            print("CBake: use build in combination with clean", file = stderr)


    if fs.debug:
        ctx.flags["DEBUG"] = True

        ctx.out_prefix = "dbg-"
        ctx.cbake_dep_file = CBAKE_DEP_FILE_DBG

    program = ctx.settings.get("program", "a.out")

    if fs.clean:
        remove(program_filename(ctx, program))
        remove(program_filename(ctx, "dbg-" + program))
        # TODO delete object files
        remove(CBAKE_DEP_FILE)
        remove(CBAKE_DEP_FILE_DBG)

    if not fs.clean or fs.build:
        success = process_files(ctx)
        if fs.test and success: success = os.system(ctx.out_prefix + program) == 0
        return (0 if success else 1)


    return 0


if __name__ == "__main__":
    from sys import argv
    exit(main(argv))


