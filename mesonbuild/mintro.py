# Copyright 2014-2016 The Meson development team

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""This is a helper script for IDE developers. It allows you to
extract information such as list of targets, files, compiler flags,
tests and so on. All output is in JSON for simple parsing.

Currently only works for the Ninja backend. Others use generated
project files and don't need this info."""

import json
from . import build, coredata as cdata
from . import mesonlib
from .ast import IntrospectionInterpreter, build_target_functions, AstConditionLevel, AstIDGenerator, AstIndentationGenerator
from . import mlog
from .backend import backends
from .mparser import FunctionNode, ArrayNode, ArgumentNode, StringNode
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
import os
import pathlib

def get_meson_info_file(info_dir: str) -> str:
    return os.path.join(info_dir, 'meson-info.json')

def get_meson_introspection_version() -> str:
    return '1.0.0'

def get_meson_introspection_required_version() -> List[str]:
    return ['>=1.0', '<2.0']

class IntroCommand:
    def __init__(self,
                 desc: str,
                 func: Optional[Callable[[], Union[dict, list]]] = None,
                 no_bd: Optional[Callable[[IntrospectionInterpreter], Union[dict, list]]] = None) -> None:
        self.desc = desc + '.'
        self.func = func
        self.no_bd = no_bd

def get_meson_introspection_types(coredata: Optional[cdata.CoreData] = None,
                                  builddata: Optional[build.Build] = None,
                                  backend: Optional[backends.Backend] = None,
                                  sourcedir: Optional[str] = None) -> Dict[str, IntroCommand]:
    if backend and builddata:
        benchmarkdata = backend.create_test_serialisation(builddata.get_benchmarks())
        testdata = backend.create_test_serialisation(builddata.get_tests())
        installdata = backend.create_install_data()
    else:
        benchmarkdata = testdata = installdata = None

    return {
        'benchmarks': IntroCommand('List all benchmarks', func=lambda: list_benchmarks(benchmarkdata)),
        'buildoptions': IntroCommand('List all build options', func=lambda: list_buildoptions(coredata), no_bd=list_buildoptions_from_source),
        'buildsystem_files': IntroCommand('List files that make up the build system', func=lambda: list_buildsystem_files(builddata)),
        'dependencies': IntroCommand('List external dependencies', func=lambda: list_deps(coredata), no_bd=list_deps_from_source),
        'scan_dependencies': IntroCommand('Scan for dependencies used in the meson.build file', no_bd=list_deps_from_source),
        'installed': IntroCommand('List all installed files and directories', func=lambda: list_installed(installdata)),
        'projectinfo': IntroCommand('Information about projects', func=lambda: list_projinfo(builddata), no_bd=list_projinfo_from_source),
        'targets': IntroCommand('List top level targets', func=lambda: list_targets(builddata, installdata, backend), no_bd=list_targets_from_source),
        'tests': IntroCommand('List all unit tests', func=lambda: list_tests(testdata)),
    }

def add_arguments(parser):
    intro_types = get_meson_introspection_types()
    for key, val in intro_types.items():
        flag = '--' + key.replace('_', '-')
        parser.add_argument(flag, action='store_true', dest=key, default=False, help=val.desc)

    parser.add_argument('--backend', choices=cdata.backendlist, dest='backend', default='ninja',
                        help='The backend to use for the --buildoptions introspection.')
    parser.add_argument('-a', '--all', action='store_true', dest='all', default=False,
                        help='Print all available information.')
    parser.add_argument('-i', '--indent', action='store_true', dest='indent', default=False,
                        help='Enable pretty printed JSON.')
    parser.add_argument('-f', '--force-object-output', action='store_true', dest='force_dict', default=False,
                        help='Always use the new JSON format for multiple entries (even for 0 and 1 introspection commands)')
    parser.add_argument('builddir', nargs='?', default='.', help='The build directory')

def list_installed(installdata):
    res = {}
    if installdata is not None:
        for t in installdata.targets:
            res[os.path.join(installdata.build_dir, t.fname)] = \
                os.path.join(installdata.prefix, t.outdir, os.path.basename(t.fname))
        for path, installpath, _ in installdata.data:
            res[path] = os.path.join(installdata.prefix, installpath)
        for path, installdir, _ in installdata.headers:
            res[path] = os.path.join(installdata.prefix, installdir, os.path.basename(path))
        for path, installpath, _ in installdata.man:
            res[path] = os.path.join(installdata.prefix, installpath)
        for path, installpath, _, _ in installdata.install_subdirs:
            res[path] = os.path.join(installdata.prefix, installpath)
    return res

def list_targets_from_source(intr: IntrospectionInterpreter) -> List[Dict[str, Union[bool, str, List[Union[str, Dict[str, Union[str, List[str], bool]]]]]]]:
    tlist = []  # type: List[Dict[str, Union[bool, str, List[Union[str, Dict[str, Union[str, List[str], bool]]]]]]]
    for i in intr.targets:
        sources = []  # type: List[str]
        for n in i['sources']:
            args = []  # type: List[Union[str, StringNode]]
            if isinstance(n, FunctionNode):
                args = list(n.args.arguments)
                if n.func_name in build_target_functions:
                    args.pop(0)
            elif isinstance(n, ArrayNode):
                args = n.args.arguments
            elif isinstance(n, ArgumentNode):
                args = n.arguments
            for j in args:
                if isinstance(j, StringNode):
                    sources += [j.value]
                elif isinstance(j, str):
                    sources += [j]

        tlist += [{
            'name': i['name'],
            'id': i['id'],
            'type': i['type'],
            'defined_in': i['defined_in'],
            'filename': [os.path.join(i['subdir'], x) for x in i['outputs']],
            'build_by_default': i['build_by_default'],
            'target_sources': [{
                'language': 'unknown',
                'compiler': [],
                'parameters': [],
                'sources': [os.path.normpath(os.path.join(os.path.abspath(intr.source_root), i['subdir'], x)) for x in sources],
                'generated_sources': []
            }],
            'subproject': None, # Subprojects are not supported
            'installed': i['installed']
        }]

    return tlist

def list_targets(builddata: build.Build, installdata, backend: backends.Backend) -> List[Dict[str, Union[bool, str, List[Union[str, Dict[str, Union[str, List[str], bool]]]]]]]:
    tlist = []  # type: List[Dict[str, Union[bool, str, List[Union[str, Dict[str, Union[str, List[str], bool]]]]]]]
    build_dir = builddata.environment.get_build_dir()
    src_dir = builddata.environment.get_source_dir()

    # Fast lookup table for installation files
    install_lookuptable = {}
    for i in installdata.targets:
        outname = os.path.join(installdata.prefix, i.outdir, os.path.basename(i.fname))
        install_lookuptable[os.path.basename(i.fname)] = str(pathlib.PurePath(outname))

    for (idname, target) in builddata.get_targets().items():
        if not isinstance(target, build.Target):
            raise RuntimeError('The target object in `builddata.get_targets()` is not of type `build.Target`. Please file a bug with this error message.')

        t = {
            'name': target.get_basename(),
            'id': idname,
            'type': target.get_typename(),
            'defined_in': os.path.normpath(os.path.join(src_dir, target.subdir, 'meson.build')),
            'filename': [os.path.join(build_dir, target.subdir, x) for x in target.get_outputs()],
            'build_by_default': target.build_by_default,
            'target_sources': backend.get_introspection_data(idname, target),
            'subproject': target.subproject or None
        }

        if installdata and target.should_install():
            t['installed'] = True
            t['install_filename'] = [install_lookuptable.get(x, None) for x in target.get_outputs()]
        else:
            t['installed'] = False
        tlist.append(t)
    return tlist

def list_buildoptions_from_source(intr: IntrospectionInterpreter) -> List[Dict[str, Union[str, bool, int, List[str]]]]:
    return list_buildoptions(intr.coredata)

def list_buildoptions(coredata: cdata.CoreData) -> List[Dict[str, Union[str, bool, int, List[str]]]]:
    optlist = []  # type: List[Dict[str, Union[str, bool, int, List[str]]]]

    dir_option_names = ['bindir',
                        'datadir',
                        'includedir',
                        'infodir',
                        'libdir',
                        'libexecdir',
                        'localedir',
                        'localstatedir',
                        'mandir',
                        'prefix',
                        'sbindir',
                        'sharedstatedir',
                        'sysconfdir']
    test_option_names = ['errorlogs',
                         'stdsplit']
    core_option_names = [k for k in coredata.builtins if k not in dir_option_names + test_option_names]

    dir_options = {k: o for k, o in coredata.builtins.items() if k in dir_option_names}
    test_options = {k: o for k, o in coredata.builtins.items() if k in test_option_names}
    core_options = {k: o for k, o in coredata.builtins.items() if k in core_option_names}

    def add_keys(options: Dict[str, cdata.UserOption], section: str, machine: str = 'any') -> None:
        for key in sorted(options.keys()):
            opt = options[key]
            optdict = {'name': key, 'value': opt.value, 'section': section, 'machine': machine}
            if isinstance(opt, cdata.UserStringOption):
                typestr = 'string'
            elif isinstance(opt, cdata.UserBooleanOption):
                typestr = 'boolean'
            elif isinstance(opt, cdata.UserComboOption):
                optdict['choices'] = opt.choices
                typestr = 'combo'
            elif isinstance(opt, cdata.UserIntegerOption):
                typestr = 'integer'
            elif isinstance(opt, cdata.UserArrayOption):
                typestr = 'array'
            else:
                raise RuntimeError("Unknown option type")
            optdict['type'] = typestr
            optdict['description'] = opt.description
            optlist.append(optdict)

    add_keys(core_options, 'core')
    add_keys(coredata.builtins_per_machine.host, 'core', machine='host')
    add_keys(
        {'build.' + k: o for k, o in coredata.builtins_per_machine.build.items()},
        'core',
        machine='build',
    )
    add_keys(coredata.backend_options, 'backend')
    add_keys(coredata.base_options, 'base')
    add_keys(coredata.compiler_options.host, 'compiler', machine='host')
    add_keys(
        {'build.' + k: o for k, o in coredata.compiler_options.build.items()},
        'compiler',
        machine='build',
    )
    add_keys(dir_options, 'directory')
    add_keys(coredata.user_options, 'user')
    add_keys(test_options, 'test')
    return optlist

def find_buildsystem_files_list(src_dir) -> List[str]:
    # I feel dirty about this. But only slightly.
    filelist = []  # type: List[str]
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f == 'meson.build' or f == 'meson_options.txt':
                filelist.append(os.path.relpath(os.path.join(root, f), src_dir))
    return filelist

def list_buildsystem_files(builddata: build.Build) -> List[str]:
    src_dir = builddata.environment.get_source_dir()
    filelist = find_buildsystem_files_list(src_dir)
    filelist = [os.path.join(src_dir, x) for x in filelist]
    return filelist

def list_deps_from_source(intr: IntrospectionInterpreter) -> List[Dict[str, Union[str, bool]]]:
    result = []  # type: List[Dict[str, Union[str, bool]]]
    for i in intr.dependencies:
        keys = [
            'name',
            'required',
            'version',
            'has_fallback',
            'conditional',
        ]
        result += [{k: v for k, v in i.items() if k in keys}]
    return result

def list_deps(coredata: cdata.CoreData) -> List[Dict[str, Union[str, List[str]]]]:
    result = []  # type: List[Dict[str, Union[str, List[str]]]]
    for d in coredata.deps.host.values():
        if d.found():
            result += [{'name': d.name,
                        'version': d.get_version(),
                        'compile_args': d.get_compile_args(),
                        'link_args': d.get_link_args()}]
    return result

def get_test_list(testdata) -> List[Dict[str, Union[str, int, List[str], Dict[str, str]]]]:
    result = []  # type: List[Dict[str, Union[str, int, List[str], Dict[str, str]]]]
    for t in testdata:
        to = {}
        if isinstance(t.fname, str):
            fname = [t.fname]
        else:
            fname = t.fname
        to['cmd'] = fname + t.cmd_args
        if isinstance(t.env, build.EnvironmentVariables):
            to['env'] = t.env.get_env({})
        else:
            to['env'] = t.env
        to['name'] = t.name
        to['workdir'] = t.workdir
        to['timeout'] = t.timeout
        to['suite'] = t.suite
        to['is_parallel'] = t.is_parallel
        to['priority'] = t.priority
        result.append(to)
    return result

def list_tests(testdata) -> List[Dict[str, Union[str, int, List[str], Dict[str, str]]]]:
    return get_test_list(testdata)

def list_benchmarks(benchdata) -> List[Dict[str, Union[str, int, List[str], Dict[str, str]]]]:
    return get_test_list(benchdata)

def list_projinfo(builddata: build.Build) -> Dict[str, Union[str, List[Dict[str, str]]]]:
    result = {'version': builddata.project_version,
              'descriptive_name': builddata.project_name,
              'subproject_dir': builddata.subproject_dir}
    subprojects = []
    for k, v in builddata.subprojects.items():
        c = {'name': k,
             'version': v,
             'descriptive_name': builddata.projects.get(k)}
        subprojects.append(c)
    result['subprojects'] = subprojects
    return result

def list_projinfo_from_source(intr: IntrospectionInterpreter) -> Dict[str, Union[str, List[Dict[str, str]]]]:
    sourcedir = intr.source_root
    files = find_buildsystem_files_list(sourcedir)
    files = [os.path.normpath(x) for x in files]

    for i in intr.project_data['subprojects']:
        basedir = os.path.join(intr.subproject_dir, i['name'])
        i['buildsystem_files'] = [x for x in files if x.startswith(basedir)]
        files = [x for x in files if not x.startswith(basedir)]

    intr.project_data['buildsystem_files'] = files
    intr.project_data['subproject_dir'] = intr.subproject_dir
    return intr.project_data

def print_results(options, results: Sequence[Tuple[str, Union[dict, List[Any]]]], indent: int) -> int:
    if not results and not options.force_dict:
        print('No command specified')
        return 1
    elif len(results) == 1 and not options.force_dict:
        # Make to keep the existing output format for a single option
        print(json.dumps(results[0][1], indent=indent))
    else:
        out = {}
        for i in results:
            out[i[0]] = i[1]
        print(json.dumps(out, indent=indent))
    return 0

def run(options) -> int:
    datadir = 'meson-private'
    infodir = 'meson-info'
    if options.builddir is not None:
        datadir = os.path.join(options.builddir, datadir)
        infodir = os.path.join(options.builddir, infodir)
    indent = 4 if options.indent else None
    results = []  # type: List[Tuple[str, Union[dict, List[Any]]]]
    sourcedir = '.' if options.builddir == 'meson.build' else options.builddir[:-11]
    intro_types = get_meson_introspection_types(sourcedir=sourcedir)

    if 'meson.build' in [os.path.basename(options.builddir), options.builddir]:
        # Make sure that log entries in other parts of meson don't interfere with the JSON output
        mlog.disable()
        backend = backends.get_backend_from_name(options.backend, None)
        intr = IntrospectionInterpreter(sourcedir, '', backend.name, visitors = [AstIDGenerator(), AstIndentationGenerator(), AstConditionLevel()])
        intr.analyze()
        # Re-enable logging just in case
        mlog.enable()
        for key, val in intro_types.items():
            if (not options.all and not getattr(options, key, False)) or not val.no_bd:
                continue
            results += [(key, val.no_bd(intr))]
        return print_results(options, results, indent)

    infofile = get_meson_info_file(infodir)
    if not os.path.isdir(datadir) or not os.path.isdir(infodir) or not os.path.isfile(infofile):
        print('Current directory is not a meson build directory.\n'
              'Please specify a valid build dir or change the working directory to it.\n'
              'It is also possible that the build directory was generated with an old\n'
              'meson version. Please regenerate it in this case.')
        return 1

    with open(infofile, 'r') as fp:
        raw = json.load(fp)
        intro_vers = raw.get('introspection', {}).get('version', {}).get('full', '0.0.0')

    vers_to_check = get_meson_introspection_required_version()
    for i in vers_to_check:
        if not mesonlib.version_compare(intro_vers, i):
            print('Introspection version {} is not supported. '
                  'The required version is: {}'
                  .format(intro_vers, ' and '.join(vers_to_check)))
            return 1

    # Extract introspection information from JSON
    for i in intro_types.keys():
        if not intro_types[i].func:
            continue
        if not options.all and not getattr(options, i, False):
            continue
        curr = os.path.join(infodir, 'intro-{}.json'.format(i))
        if not os.path.isfile(curr):
            print('Introspection file {} does not exist.'.format(curr))
            return 1
        with open(curr, 'r') as fp:
            results += [(i, json.load(fp))]

    return print_results(options, results, indent)

updated_introspection_files = []  # type: List[str]

def write_intro_info(intro_info: Sequence[Tuple[str, Union[dict, List[Any]]]], info_dir: str) -> None:
    global updated_introspection_files
    for i in intro_info:
        out_file = os.path.join(info_dir, 'intro-{}.json'.format(i[0]))
        tmp_file = os.path.join(info_dir, 'tmp_dump.json')
        with open(tmp_file, 'w') as fp:
            json.dump(i[1], fp)
            fp.flush() # Not sure if this is needed
        os.replace(tmp_file, out_file)
        updated_introspection_files += [i[0]]

def generate_introspection_file(builddata: build.Build, backend: backends.Backend) -> None:
    coredata = builddata.environment.get_coredata()
    intro_types = get_meson_introspection_types(coredata=coredata, builddata=builddata, backend=backend)
    intro_info = []  # type: List[Tuple[str, Union[dict, List[Any]]]]

    for key, val in intro_types.items():
        if not val.func:
            continue
        intro_info += [(key, val.func())]

    write_intro_info(intro_info, builddata.environment.info_dir)

def update_build_options(coredata: cdata.CoreData, info_dir) -> None:
    intro_info = [
        ('buildoptions', list_buildoptions(coredata))
    ]

    write_intro_info(intro_info, info_dir)

def split_version_string(version: str) -> Dict[str, Union[str, int]]:
    vers_list = version.split('.')
    return {
        'full': version,
        'major': int(vers_list[0] if len(vers_list) > 0 else 0),
        'minor': int(vers_list[1] if len(vers_list) > 1 else 0),
        'patch': int(vers_list[2] if len(vers_list) > 2 else 0)
    }

def write_meson_info_file(builddata: build.Build, errors: list, build_files_updated: bool = False) -> None:
    global updated_introspection_files
    info_dir = builddata.environment.info_dir
    info_file = get_meson_info_file(info_dir)
    intro_types = get_meson_introspection_types()
    intro_info = {}

    for i in intro_types.keys():
        if not intro_types[i].func:
            continue
        intro_info[i] = {
            'file': 'intro-{}.json'.format(i),
            'updated': i in updated_introspection_files
        }

    info_data = {
        'meson_version': split_version_string(cdata.version),
        'directories': {
            'source': builddata.environment.get_source_dir(),
            'build': builddata.environment.get_build_dir(),
            'info': info_dir,
        },
        'introspection': {
            'version': split_version_string(get_meson_introspection_version()),
            'information': intro_info,
        },
        'build_files_updated': build_files_updated,
    }

    if errors:
        info_data['error'] = True
        info_data['error_list'] = [x if isinstance(x, str) else str(x) for x in errors]
    else:
        info_data['error'] = False

    # Write the data to disc
    tmp_file = os.path.join(info_dir, 'tmp_dump.json')
    with open(tmp_file, 'w') as fp:
        json.dump(info_data, fp)
        fp.flush()
    os.replace(tmp_file, info_file)
