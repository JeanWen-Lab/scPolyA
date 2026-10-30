"""Closed dispatch table for the deterministic GUI analysis backends."""
from importlib import import_module

_MODULES = {'cs1': 'scpolya_ui.analysis.cs1_backend',
            'cs2': 'scpolya_ui.analysis.cs2_backend',
            'cs3': 'scpolya_ui.analysis.cs3_backend'}


def backend(method: str):
    key = method.split('.',1)[0]
    if key not in _MODULES:
        raise ValueError('Unknown analysis method')
    return import_module(_MODULES[key])


def capabilities():
    methods = []
    for name in _MODULES.values():
        try:
            module = import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name == name:
                continue
            raise
        methods.extend(module.capabilities())
    return methods


def plan(request):
    method=request['method']
    payload=dict(request)
    if method.startswith('cs2.'):
        payload.pop('method')
    return backend(method).plan(payload)


def execute(preview, output_dir):
    return backend(preview['method']).execute(preview, output_dir)


def result(method, output_dir):
    return backend(method).result(output_dir)
