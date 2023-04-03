# CBake
Simple C/C++ building tool, with include-dependency checking

# `Add CBake to a repository`

To add CBake to your repository, add CBake as a submodule:

```
git submodule add -- https://github.com/manuel-fischer/CBake CBake
```

To create a shortcut command and a basic bakefile, if it did not already exist in the current directory:

```
python CBake/cbake-setup.py
```


# `bakefile.json`

The `bakefile.json` contains all the necessary settings specific to the program.

`program`: A string which refers to the filename of the executable.

**Build Statistics:**

`build-stats-file`: Specify filename (default `null`), to which
build statistics are written in a readable CSV-format. If `null` is specified, no statistics are generated.

**Compiler Arguments:**

`c-flags`: Compiler settings that are used for compiling an object file from a C source.

`cxx-flags`: Compiler settings that are used for compiling an object file from a C++ source.

`linker-flags`: Compiler settings that are used in the final linking step.

All of these above can be strings or arrays of strings.
An array of strings is handled in a special way. Any element, that starts with `@` is
a conditional element, that is only included if the condition between the `@` and `:`
evaluates to true. The condition is composed of multiple literals connected by `&`.
Each literal can start with an `!`, this means that the value of flag gets inverted.

For example, the `-luser32` parameter is only passed to the compiler, if the WIN-Flag is active, that
is if the current platform is a windows platform:

```json
"@WIN: -luser32"
```

**Example**

```json
{
  "program": "test",
  "c-flags": [
    "-std=c17",
    "-O2",
    "@!DEBUG: -DNDEBUG"
  ],
  "linker-flags": [
    "@WIN: -luser32"
  ]
}
```

# Flags
`WIN`: The current system is a windows platform

`DEBUG`: CBake has been called with the `debug` option

`<NUMBER>` (`32`, `64`): The architecture/address width


# CBake
```
cbake.py help | [debug] [test] | clear
```

`help`: Shows this help.

`debug`: Enables the debugging target. The program filename is prefixed with `dbg-`. It sets the `DEBUG` flag to true.

`test`: Run the program after compilation.

`clear`: Delete the executable and the dependency cache.



# `.gitignore`
You might need to add the following lines to your `.gitignore` file.

```
.cbake-dependencies.txt
.cbake-dependencies-dbg.txt
```
