# -*- coding: utf-8 -*-
import dataclasses
import importlib
import json
from pathlib import Path
import types
import inspect

# Nipype import
try:
    from nipype.interfaces.base import Interface as NipypeInterface
# If nipype is not found create a dummy Interface class
except ImportError:
    NipypeInterface = type("Interface", (object, ), {})

from soma.controller import field, Controller
from soma.undefined import undefined
from soma.singleton import Singleton

from .config.configuration import ApplicationConfiguration
from .dataset import Dataset
from .pipeline.pipeline import Pipeline, CustomPipeline
from .pipeline.process_iteration import ProcessIteration
from .process.process import Process, Node
from .process.nipype_process import nipype_factory



class Capsul(Singleton):
    '''User entry point to Capsul features. 
    This objects reads Capsul configuration in site and user environments.
    It allows configuration customization and instanciation of a 
    CapsulEngine instance to reach an execution environment.
    
    Example:

        from capsul.api import Capsul
        capsul = Capsul()
        e = capsul.executable('capsul.process.test.test_runprocess.DummyProcess')
        with capsul.engine() as capsul_engine:
            capsul_engine.run(e)

    '''    

    def __singleton_init__(self, app_name='capsul', site_file=None):
        if isinstance(site_file, Path):
            site_file=str(site_file)
        c = ApplicationConfiguration(app_name=app_name, site_file=site_file)
        self.config = c.merged_config

    @staticmethod
    def is_executable(item):
        '''Check if the input item is a process class or function with decorator
        '''
        if isinstance(item, type) and item not in (Pipeline, Process) \
                and (issubclass(item, Process) or issubclass(item, NipypeInterface)):
            return True
        if not inspect.isfunction(item):
            return False
        if item.__annotations__:
            return True
        return False

    @staticmethod
    def executable(definition, **kwargs):
        ''' Get an "executable" instance
        (:class:`~capsul.process.process.Process`,
        :class:`~capsul.pipeline.pipeline.Pipeline...)` from its module or file
        name.
        '''
        return executable(definition, **kwargs)

    def engine(self, name='local'):
        ''' Get a :class:`~capsul.engine.CapsulEngine` instance
        '''
        # Avoid circular import
        from .engine.local import LocalEngine

        engine_config = self.config.get(name, {})
        return LocalEngine(name, engine_config)

    def dataset(self, path):
        ''' Get a :class:`~.dataset.DataSet` instance associated with the given path

        Parameters
        ----------
        path: :class:`pathlib.Path`
            path for the dataset
        '''
        return Dataset(path)

    @staticmethod
    def custom_pipeline():
        return CustomPipeline()

    def iteration_pipeline(self, executable,
            non_iterative_plugs=None, 
            iterative_plugs=None, 
            do_not_export=None,
            make_optional=None,
            **kwargs):
            """ Create a pipeline with an iteration node iterating the given
            process.
            Parameters
            ----------
            pipeline_name: str
                pipeline name
            node_name: str
                iteration node name in the pipeline
            process_or_id: process description
                as in :meth:`get_process_instance`
            iterative_plugs: list (optional)
                passed to :meth:`Pipeline.add_iterative_process`
            do_not_export: list
                passed to :meth:`Pipeline.add_iterative_process`
            make_optional: list
                passed to :meth:`Pipeline.add_iterative_process`
            Returns
            -------
            pipeline: :class:`Pipeline` instance
            """
            from capsul.pipeline.pipeline import Pipeline

            pipeline = self.custom_pipeline()
            pipeline.add_iterative_process('iteration', executable,
                non_iterative_plugs=non_iterative_plugs,
                iterative_plugs=iterative_plugs,
                do_not_export=do_not_export,
                make_optional=make_optional,
                **kwargs)
            pipeline.autoexport_nodes_parameters(include_optional=True)
            return pipeline


def executable(definition, **kwargs):
    '''
    Build a Process instance given a definition item.
    This definition item can be :
      - A dictionary containing the JSON serialization of a process.
        A new instance is created by desierializing this dictionary.
      - A process instance.
    '''        
    result = None
    item = None
    if isinstance(definition, dict):
        result = executable_from_json(None, definition)
    elif isinstance(definition, Process):
        if kwargs:
            raise ValueError('executable() do not allow to modify parameters '
                             'of an existing process')
        return definition
    elif isinstance(definition, type) and issubclass(definition, Process):
        # A class is given as definition. Check that it can be imported.
        module_name = definition.__module__
        object_name = definition.__name__
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            raise TypeError(
                f'Class {definition} cannot be used to create a Process '
                'beacause its module cannot be imported : {e}')
        cls = getattr(module, object_name, None)
        if cls is not definition:
            raise TypeError(
                f'Class {definition} cannot be used to create a Process '
                f'beacause variable {object_name} of module {module_name} '
                f'contains {cls}')
        result = definition(definition=f'{module_name}.{object_name}')
    else:
        if definition.endswith('.json'):
            with open(definition) as f:
                json_executable = json.load(f)
            result =  executable_from_json(definition, json_executable)
        elif definition.endswith('.py') or len(definition.rsplit('.py#')) == 2:
            filename = definition
            object_name = None
            def_item = definition.rsplit('.py#')
            if len(def_item) == 2:
                # class/function name after # in the definition
                object_name = def_item[1]
                filename = def_item[0] + '.py'
            d = {}
            with open(filename) as f:
                exec(f.read(), d, d)
            if object_name:
                process = d.get(object_name)
                if process is None:
                    raise ValueError(
                        f'Invalid executable definition: {definition}')
            else:
                processes = [i for i in d.values()
                             if isinstance(i, type) and issubclass(i, Node)
                                and i not in (Node, Process, Pipeline)]
                if not processes:
                    # TODO: try to find a function process
                    raise ValueError(f'No process class found in {definition}')
                if len(processes) > 1:
                    raise ValueError(
                        f'Several process classes found in {definition}')
                process = processes[0]
            result = executable_from_python(definition, process)
        else:
            elements = definition.rsplit('.', 1)
            if len(elements) > 1:
                module_name, object_name = elements
                module = importlib.import_module(module_name)
                item = getattr(module, object_name, None)
                if item is None:
                    # maybe a sub-module to be imported
                    module = importlib.import_module(definition)
                    if module is not None:
                        item = module
                # check if item is a module containing a single process
                if (inspect.ismodule(item)
                        or (inspect.isclass(item)
                            and not issubclass(item, Node))):
                    items = find_executables(item)
                    if len(items) == 1:
                        module = item
                        item = items[0]
                        definition = '.'.join((definition,
                                               item.__name__.rsplit('.')[-1]))
                    elif len(items) > 1:
                        raise ValueError(
                            f'Several process classes found in {definition}')
                result = executable_from_python(definition, item)

    if result is not None:
        for name, value in kwargs.items():
            setattr(result, name, value)
        return result
    raise ValueError(f'Invalid executable definition: {definition}') 
    
def find_executables(module):
    ''' Look for "executables" (:class:`~.process.node.Node` classes) in the
    given module
    '''
    # report only Node classes, since it's difficult to be sure a function
    # is meant to be a Process or not.
    items = []
    for item in module.__dict__.values():
        if inspect.isclass(item) and issubclass(item, Node) \
                and item not in (Node, Process, Pipeline):
            items.append(item)
    return items

def executable_from_python(definition, item):
    '''
    Build a process instance from a Python object and its definition string.
    '''
    result = None
    # If item is already a Process
    # instance.
    if isinstance(item, Process):
        result = item

    # If item is a Process class.
    elif (isinstance(item, type) and
        issubclass(item, Process)):
        result = item(definition=definition)

    # If item is a Nipye
    # interface instance, wrap this structure in a Process class
    elif isinstance(item, NipypeInterface):
        result = nipype_factory(definition, item)

    # If item is a Nipype Interface class.
    elif (isinstance(item, type) and
        issubclass(item, NipypeInterface)):
        result = nipype_factory(item())

    # If item is a function.
    elif isinstance(item, types.FunctionType):
        annotations = getattr(item, '__annotations__', None)
        if annotations:
            result = process_from_function(item)(definition=definition)
        else:
            raise ValueError(f'Cannot find annotation description to make function {item} a process')

    else:
        raise ValueError(f'Cannot create an executable from {item}')           

    return result

def executable_from_json(definition, json_executable):
    '''
    Build a process instance from a JSON dictionary and its definition string.
    '''
    process_definition = json_executable.get('definition')
    if process_definition is None:
        raise ValueError(f'definition missing from process defined in {definition}')
    type = json_executable.get('type')
    if type is None:
        raise ValueError(f'type missing from process defined in {definition}')
    if definition is None:
        definition = json_executable.get('definition')
    if type in ('process', 'pipeline'):
        result = executable(process_definition)
        if definition is not None:
            result.definition = definition
    elif type == 'custom_pipeline':
        result = CustomPipeline(definition=definition, 
                                json_executable=process_definition)
    elif type == 'iterative_process':
        result = ProcessIteration(
            definition=definition,
            process=executable(process_definition['process']),
            iterative_parameters=process_definition['iterative_parameters'],
            context_name=process_definition ['context_name'],
        )
    else:
        raise ValueError(f'Invalid executable type in {definition}: {type}')
    parameters = json_executable.get('parameters')
    if parameters:
        result.import_json(parameters)
    return result

def process_from_function(function):
    '''
    Build a process instance from an annotated function.
    '''
    annotations = {}
    for name, type_ in getattr(function, '__annotations__', {}).items():
        output = name == 'return'
        if isinstance(type_, dataclasses.Field):
            metadata = {}
            metadata.update(type_.metadata)
            metadata.update(type_.metadata.get('_metadata', {}))
            if '_metadata' in metadata:
                del metadata['_metadata']
            metadata['output'] = output
            default=type_.default
            default_factory=type_.default_factory
            kwargs = dict(
                type_=type_.type,
                default=default,
                default_factory=default_factory,
                repr=type_.repr,
                hash=type_.hash,
                init=type_.init,
                compare=type_.compare,
                metadata=metadata)
        else:
            kwargs = dict(
                type_=type_,
                default=undefined,
                output=output)
        if output:
            # "return" cannot be used as a parameter because it is a Python keyword.
            #  Change it to "result"
            name = 'result'
        annotations[name] = field(**kwargs)

    def wrap(self, context):
        kwargs = {i: getattr(self, i) for i in annotations if i != 'result' and getattr(self, i, undefined) is not undefined}
        result = function(**kwargs)
        setattr(self, 'result', result)

    namespace = {
        '__annotations__': annotations,
        'execute': wrap,
    }
    name = f'{function.__name__}_process'
    return type(name, (Process,), namespace)


def get_node_class(node_type):
    """
    Get a custom node class from module + class string.
    The class name is optional if the module contains only one node class.
    It is OK to pass a Node subclass or a Node instance also.
    """
    if inspect.isclass(node_type):
        if issubclass(node_type, Node):
            return node_type.__name__, node_type  # already a Node class
        return Node
    if isinstance(node_type, Node):
        return node_type.__class__.__name__, node_type.__class__
    cls = None
    try:
        mod = importlib.import_module(node_type)
        for name, val in mod.__dict__.items():
            if inspect.isclass(val) and val.__name__ != 'Node' \
                    and issubclass(val, Node):
                cls = val
                break
        else:
            return None
    except ImportError:
        name = node_type.split('.')[-1]
        modname = node_type[:-len(name) - 1]
        mod = importlib.import_module(modname)
        cls = getattr(mod, name)
    if cls is None:
        return None
    return name, cls


def get_node_instance(node_type, pipeline, conf_dict=None, name=None,
                      **kwargs):
    """
    Get a custom node instance from a module + class name (see
    :func:`executable`) and a configuration dict or Controller.
    The configuration contains parameters needed to instantiate the node type.
    Each node class may specify its parameters via its class method
    `configure_node`.

    Parameters
    ----------
    node_type: str or Node subclass or Node instance
        node type to be built. Either a class (Node subclass) or a Node
        instance (the node will be re-instantiated), or a string
        describing a module and class.
    pipeline: Pipeline
        pipeline in which the node will be inserted.
    conf_dict: dict or Controller
        configuration dict or Controller defining parameters needed to build
        the node. The controller should be obtained using the node class's
        `configure_node()` static method, then filled with the desired values.
        If not given the node is supposed to be built with no parameters, which
        will not work for every node type.
    kwargs:
        default values of the node instance parameters.
    """
    cls_and_name = get_node_class(node_type)
    if cls_and_name is None or issubclass(cls_and_name[1], Process):
        raise ValueError("Could not find node class %s" % node_type)
    nname, cls = cls_and_name
    if not name:
        name = nname

    if isinstance(conf_dict, Controller):
        conf_controller = conf_dict
    elif conf_dict is not None:
        if hasattr(cls, 'configure_controller'):
            conf_controller = cls.configure_controller()
            if conf_controller is None:
                raise ValueError("node type %s has a configuration controller "
                                 "problem (see %s.configure_controller()"
                                 % (node_type, node_type))
            conf_controller.import_dict(conf_dict)
        else:
            conf_controller = Controller()
    else:
        if hasattr(cls, 'configure_controller'):
            conf_controller = cls.configure_controller()
        else:
            conf_controller = Controller()
    if hasattr(cls, 'build_node'):
        node = cls.build_node(pipeline, name, conf_controller)
    else:
        # probably bound to fail...
        node = cls(pipeline, name, [], [])

    # Set the instance default parameters
    for name, value in kwargs.items():
        setattr(node, name, value)

    return node

_interface = None
_nipype_loaded = False

def _get_interface_class():
    '''
    returns the nypype Interface type, or a custom type if it cannot be
    imported.
    We use this function on demand because importing nipype is long (it
    sometimes takes several seconds) and it's not always needed.
    We don't really import nipype, but use sys.modules to get it instead,
    because here we only use Interface to check if a given object is an
    instance of Interface.
    '''
    global _interface, _nipype_loaded
    if _interface is not None and _nipype_loaded:
        return _interface
    if not _nipype_loaded:
        # Nipype import
        nipype = sys.modules.get('nipype.interfaces.base')
        if nipype is None:
            _interface = type("Interface", (object, ), {})
        else:
            _interface = getattr(nipype, 'Interface')
            _nipype_loaded = True
    return _interface

def is_executable(item):
    """ Check if the input item is a process class or function with annotations
    """
    Interface = _get_interface_class()
    if inspect.isclass(item) and item not in (Pipeline, Process) \
            and (issubclass(item, Process) or issubclass(item, Interface)):
        return True
    if not inspect.isfunction(item):
        return False
    if hasattr(item, '__annotations__'):
        return True
    return False