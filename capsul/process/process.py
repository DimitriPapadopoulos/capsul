# -*- coding: utf-8 -*-
''' Process main class and infrastructure

Classes
-------

.. currentmodule:: capsul.process.process

:class:`Process`
++++++++++++++++
:class:`FileCopyProcess`
++++++++++++++++++++++++
:class:`InteractiveProcess`
+++++++++++++++++++++++++++
:class:`NipypeProcess`
++++++++++++++++++++++
:class:`ProcessMeta`
++++++++++++++++++++
:class:`ProcessResult`
++++++++++++++++++++++

'''

import os
from datetime import datetime as datetime
import shutil
import sys
import glob
import tempfile
import traceback


from soma.controller import (Controller, undefined, file, directory)
from .node import Node


class Process(Node):
    """ A process is an atomic component that contains a processing.

    A process is typically an object with typed parameters, and an execution
    function. Parameters are described using the
    :somabase:`Controller <api.html#soma.controller.controller.Controller>`
    API, based on the dataclasses
    module. Parameters are fields which allow typechecking and notification.

    In addition to describing its parameters, a Process must implement its
    execution function, either through a python method, by overloading
    :meth:`execute`, or through a commandline execution, by overloading
    :meth:`get_commandline`. The second way allows to run on a remote
    processing machine which has not necessary capsul, nor python, installed.

    Parameters are declared or queried using the fields API, and their values
    are in the process instance variables:

    ::

        from capsul.api import Process
        from capsul.run import run
        from soma.controller import file

        class MyProcess(Process):

            # a class parameter
            param1: str = 'def_param1'

            def __init__(self):
                super().__init__()
                # declare an input param
                self.add_field('param2', int)
                # declare an output param
                self.add_field('out_param', file(output=True))

            def execute(self):
                with open(self.out_param, 'w') as f:
                    print('param1:', self.param1, file=f)
                    print('param2:', self.param2, file=f)

        # run it with parameters
        run(MyProcess, param2=12, out_param='/tmp/log.txt')

    **Note about the file and directory parameters**

    The :class:`~traits.trait_types.File` trait type represents a file
    parameter. A file is actually two things: a filename (string), and the file itself (on the filesystem). For an input it is OK not to distinguish them, but for an output, there are two different cases:

    * the file (on the filesystem) is an output, but the filename (string) is
      given as an input: this is the classical "commandline" behavior, when we
      tell the program where it should write its output file.
    * the file is an output, and the filename is also an output: this is rather a
      "function return value" behavior: the process determines internally where
      it should write the file, and tells as an output where it did.

    To distinguish these two cases, in Capsul we normally add in the
    :class:`~soma.controller.file` or :class:`~soma.controller.directory`
    fields a metadata ``input_filename`` which is True when the filename is an
    input, and False when the filename is an output::

        self.add_field('out_file', file, output=True, input_filename=False)

    However as most of our processes are based on the "commandline behavior"
    (the filename is an input) and we often forget to specify the
    ``input_filename`` parameter, the default is the "filename is an input"
    behavior, when not specified.

    **Attributes**

    Attributes
    ----------
    name: str
        the class name.
    id: str
        the string description of the class location (ie., module.class).
    """

    def __init__(self, definition=None):
        super().__init__(name=definition)
        self.definition = definition
    
    def json(self):
        result = {
            'definition': self.definition,
            'parameters': super().json(),
        }
        return result

    def before_execute(self):
        """This method is called by CapsulEngine before calling
        execute(). By default it does nothing but can be overriden
        in derived classes.
        """
        pass

    def after_execute(self):
        """This method is called by CapsulEngine after calling
        execute(). By default it does nothing but can be overridden
        in derived classes.
        """
        pass        
 
    def get_missing_mandatory_parameters(self):
        ''' Returns a list of parameters which are not optional, and which
        value is Undefined or None, or an empty string for a file or
        Directory parameter.
        '''

        missing = []
        for field in self.fields():
            optional = field.metadata.get('optional', False)
            if optional:
                output = field.metadata.get('output', False)
                value = getattr(self, field.name, undefined)
                if not output and value in (undefined, None):
                    missing.append(field.name)
        return missing

    def is_job(self):
        return True

    def execute(self):
        raise NotImplementedError(f'The run method is not implemented for process {self.definition}')


class FileCopyProcess(Process):
    """ A specific process that copies all the input files.

    Attributes
    ----------
    copied_inputs : dict
        the list of copied file parameters {param: dst_value}
    copied_files: dict
        copied files {param: [dst_value1, ...]}

    Methods
    -------
    _update_input_traits
    _get_process_arguments
    _copy_input_files
    """
    def __init__(self, activate_copy=True, inputs_to_copy=None,
                 inputs_to_clean=None, destination=None,
                 inputs_to_symlink=None, use_temp_output_dir=False):
        """ Initialize the FileCopyProcess class.

        Parameters
        ----------
        activate_copy: bool (default True)
            if False this class is transparent and behaves as a Process class.
        inputs_to_copy: list of str (optional, default None)
            the list of inputs to copy.
            If None, all the input files are copied.
        inputs_to_clean: list of str (optional, default None)
            some copied inputs that can be deleted at the end of the
            processing. If None, all copied files will be cleaned.
        destination: str (optional default None)
            where the files are copied.
            If None, the output directory will be used, unless
            use_temp_output_dir is set.
        inputs_to_symlink: list of str (optional, default None)
            as inputs_to_copy, but for files which should be symlinked
        use_temp_output_dir: bool
            if True, the output_directory parameter is set to a temp one during
            execution, then outputs are copied / moved / hardlinked to the
            final location. This is useful when several parallel jobs are
            working in the same directory and may write the same intermediate
            files (SPM does this a lot).
        """
        super().__init__()

        # Class parameters
        self.activate_copy = activate_copy
        self.destination = destination
        if self.activate_copy:
            self.inputs_to_clean = inputs_to_clean
            if inputs_to_symlink is None:
                self.inputs_to_symlink = list(self.user_traits().keys())
            else:
                self.inputs_to_symlink = inputs_to_symlink
            if inputs_to_copy is None:
                self.inputs_to_copy = [k for k in self.user_traits().keys()
                                       if k not in self.inputs_to_symlink]
            else:
                self.inputs_to_copy = inputs_to_copy
                self.inputs_to_symlink = [k for k in self.inputs_to_symlink
                                          if k not in self.inputs_to_copy]
            self.copied_inputs = None
            self.copied_files = None
        self.use_temp_output_dir = use_temp_output_dir

    def _before_run_process(self):
        """ Method to copy files before executing the process.
        """
        super(FileCopyProcess, self)._before_run_process()

        if self.destination is None:
            output_directory = getattr(self, 'output_directory', None)
            if output_directory in (None, undefined, ''):
                output_directory = None
            if self.use_temp_output_dir:
                workspace = tempfile.mkdtemp(dir=output_directory,
                                             prefix=self.name)
                destdir = workspace
            else:
                destdir = output_directory
        else:
            destdir = self.destination
        if not destdir:
            raise ValueError('FileCopyProcess cannot be used without a '
                             'destination directory')
        self._destination = destdir
        output_directory = self.destination
        if output_directory is None:
            output_directory = getattr(self, 'output_directory', None)
        if output_directory not in (None, undefined, ''):
            self._former_output_directory = output_directory
            self.output_directory = destdir

        # The copy option is activated
        if self.activate_copy:

            # Copy the desired items
            self._update_input_traits()

            self._recorded_params = {}
            # Set the process inputs
            for name, value in self.copied_inputs.items():
                self._recorded_params[name] = getattr(self, name)
                self.set_parameter(name, value)

    def _after_run_process(self, run_process_result):
        """ Method to clean-up temporary workspace after process
        execution.
        """
        run_process_result = super(FileCopyProcess, self)._after_run_process(
            run_process_result)
        if self.use_temp_output_dir:
            self._move_outputs()
        # The copy option is activated
        if self.activate_copy:
            # Clean the workspace
            self._clean_workspace()

        # restore initial values, keeping outputs
        # The situation here is that:
        # * output_directory should drive "final" output valules
        # * we may have been using a temporary output directory, thus output
        #   values are already set to this temp dir, not the final one.
        #   (at least when use_temp_output_dir is set).
        #   -> _move_outputs() already changes these output values
        # * when we reset inputs, outputs are reset to values pointing to
        #   the input directory (via nipype callbacks).
        # So we must:
        # 1. record output values
        # 2. set again inputs to their initial values (pointing to the input
        #    directories). Outputs will be reset accordingly to input dirs.
        # 3. force output values using the recorded ones.

        # 1. record output values
        outputs = {}
        for name, trait in self.user_traits().items():
            if trait.output:
                outputs[name] = getattr(self, name)
        # 2. set again inputs to their initial values
        if hasattr(self, '_recorded_params'):
            for name, value in self._recorded_params.items():
                self.set_parameter(name, value)
        # 3. force output values using the recorded ones
        for name, value in outputs.items():
            self.set_parameter(name, value)
        if hasattr(self, '_recorded_params'):
            del self._recorded_params

        return run_process_result

    def _clean_workspace(self):
        """ Removed some copied inputs that can be deleted at the end of the
        processing.
        """
        inputs_to_clean = self.inputs_to_clean
        if inputs_to_clean is None:
            # clean all copied inputs
            inputs_to_clean = list(self.copied_files.keys())
        for to_rm_name in inputs_to_clean:
            rm_files = self.copied_files.get(to_rm_name, [])
            if rm_files:
                self._rm_files(rm_files)
                del self.copied_files[to_rm_name]
                del self.copied_inputs[to_rm_name]

    def _move_outputs(self):
        tmp_output = self._destination
        dst_output = self._former_output_directory
        output_values = {}
        moved_dict = {}
        for param, trait in self.user_traits().items():
            if trait.output:
                new_value = self._move_files(tmp_output, dst_output,
                                             getattr(self, param),
                                             moved_dict=moved_dict)
                output_values[param] = new_value
                self.set_parameter(param, new_value)

        shutil.rmtree(tmp_output)
        del self._destination
        self.destinaton = self._former_output_directory
        if hasattr(self, 'output_directory'):
            self.output_directory = self._former_output_directory
        del self._former_output_directory
        return output_values

    def _move_files(self, src_directory, dst_directory, value, moved_dict={}):
        if isinstance(value, (list, tuple)):
            new_value = [self._move_files(src_directory, dst_directory, item,
                                          moved_dict)
                         for item in value]
            if isinstance(value, tuple):
                return tuple(new_value)
            return new_value
        elif isinstance(value, dict):
            new_value = {}
            for name, item in value.items():
                new_value[name] = self._move_files(
                    src_directory, dst_directory, item, moved_dict)
            return new_value
        elif isinstance(value, str):
            if value in moved_dict:
                return moved_dict[value]
            if os.path.dirname(value) == src_directory \
                    and os.path.exists(value):
                name = os.path.basename(value).split('.')[0]
                matfnames = glob.glob(os.path.join(
                    os.path.dirname(value), name + ".*"))
                todo = [x for x in matfnames if x != value]
                dst = os.path.join(dst_directory, os.path.basename(value))
                if os.path.exists(dst) or os.path.islink(dst):
                    print('warning: file or directory %s exists' % dst)
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    else:
                        os.unlink(dst)
                try:
                    # print('moving:', value, 'to:', dst)
                    shutil.move(value, dst)
                except Exception as e:
                    print(e, file=sys.stderr)
                moved_dict[value] = dst
                for item in todo:
                    self._move_files(src_directory, dst_directory, item,
                                     moved_dict)
                return dst
        return value

    def _rm_files(self, python_object):
        """ Remove a set of copied files from the filesystem.

        Parameters
        ----------
        python_object: object
            a generic python object.
        """
        # Deal with dictionary
        if isinstance(python_object, dict):
            for val in python_object.values():
                self._rm_files(val)

        # Deal with tuple and list
        elif isinstance(python_object, (list, tuple)):
            for val in python_object:
                self._rm_files(val)

        # Otherwise start the deletion if the object is a file
        else:
            if (isinstance(python_object, str) and
                    os.path.isfile(python_object)):
                os.remove(python_object)

    def _update_input_traits(self, copy=True):
        """ Update the process input traits: input files are copied.
        """
        # Get the new trait values
        input_parameters, input_symlinks = self._get_process_arguments()
        self.copied_files = {}
        self.copied_inputs \
            = self._copy_input_files(input_parameters, False,
                                     self.copied_files, copy=copy)
        self.copied_inputs.update(
            self._copy_input_files(input_symlinks, True, self.copied_files,
                                   copy=copy))

    def _copy_input_files(self, python_object, use_symlink=True,
                          files_list=None, copy=True):
        """ Recursive method that copy the input process files.

        Parameters
        ----------
        python_object: object
            a generic python object.
        use_symlink: bool
            if True, symbolic links will be make instead of full copies, on
            systems which support symlinks. Much faster and less disk consuming
            than full copies, but might have side effects in programs which
            detect and handle symlinks in a different way.
        files_list: list or dict
            if provided, this *output* parameter will be updated with the list
            of files actually created.
        copy: bool
            if False, files are not actually copied/symlinked but filenames are
            generated

        Returns
        -------
        out: object
            the copied-file input object.
        """
        if sys.platform.startswith('win') and sys.version_info[0] < 3:
            # on windows, no symlinks (in python2 at least).
            use_symlink = False

        # Deal with dictionary
        # Create an output dict that will contain the copied file locations
        # and the other values
        if isinstance(python_object, dict):
            out = {}
            for key, val in python_object.items():
                if val is not undefined:
                    if isinstance(files_list, dict):
                        sub_files_list = files_list.setdefault(key, [])
                    else:
                        sub_files_list = files_list
                    out[key] = self._copy_input_files(val, use_symlink,
                                                      sub_files_list,
                                                      copy=copy)

        # Deal with tuple and list
        # Create an output list or tuple that will contain the copied file
        # locations and the other values
        elif isinstance(python_object, (list, tuple)):
            out = []
            for val in python_object:
                if val is not undefined:
                    out.append(self._copy_input_files(val, use_symlink,
                                                      files_list, copy=copy))
            if isinstance(python_object, tuple):
                out = tuple(out)

        # Otherwise start the copy (with metadata cp -p) if the object is
        # a file
        else:
            out = python_object
            if (python_object is not undefined and
                    isinstance(python_object, str) and
                    os.path.isfile(python_object)):
                destdir = self._destination
                if not os.path.exists(destdir):
                    os.makedirs(destdir)
                fname = os.path.basename(python_object)
                out = os.path.join(destdir, fname)
                if out == python_object:
                    return out  # input=output, nothing to do
                if copy:
                    if os.path.exists(out) or os.path.islink(out):
                        if os.path.isdir(out):
                            shutil.rmtree(out)
                        else:
                            os.unlink(out)
                    if use_symlink:
                        os.symlink(python_object, out)
                    else:
                        shutil.copy2(python_object, out)
                    if files_list is not None:
                        files_list.append(out)

                    # Copy associated .mat/.json/.minf files
                    name = fname.rsplit(".", 1)[0]
                    matfnames = glob.glob(os.path.join(
                        os.path.dirname(python_object), name + ".*"))
                    for matfname in matfnames:
                        extrafname = os.path.basename(matfname)
                        extraout = os.path.join(destdir, extrafname)
                        if extraout != out:
                            if os.path.exists(extraout) \
                                    or os.path.islink(extraout):
                                if os.path.isdir(extraout):
                                    shutil.rmtree(extraout)
                                else:
                                    os.unlink(extraout)
                            if use_symlink:
                                os.symlink(matfname, extraout)
                            else:
                                shutil.copy2(matfname, extraout)
                            if files_list is not None:
                                files_list.append(extraout)

        return out

    def _get_process_arguments(self):
        """ Get the process arguments.

        The user process traits are accessed through the user_traits()
        method that returns a sorted dictionary.

        Returns
        -------
        input_parameters: dict
            the process input parameters that should be copied.
        input_symlinks: dict
            the process input parameters that should be symlinked.
        """
        # Store for input parameters
        input_parameters = {}
        input_symlinks = {}

        # Go through all the user traits
        for name, trait in self.user_traits().items():
            if trait.output:
                continue
            # Check if the target parameter is in the check list
            c = name in self.inputs_to_copy
            s = name in self.inputs_to_symlink
            if c or s:
                # Get the trait value
                value = getattr(self, name, undefined)
                # Skip undefined field attributes and outputs
                if value is not undefined:
                    # Store the input parameter
                    if c:
                        input_parameters[name] = value
                    else:
                        input_symlinks[name] = value

        return input_parameters, input_symlinks


class NipypeProcess(FileCopyProcess):
    """ Base class used to wrap nipype interfaces.
    """

    def __new__(cls, *args, **kwargs):

        def init_with_skip(self, *args, **kwargs):

            cls = self.__init__.cls
            init_att = '__%s_np_init_done__' % cls.__name__
            if hasattr(self, init_att) and getattr(self, init_att):
                # may be called twice, from within __new__ or from python
                # internals
                return

            setattr(self, init_att, True)
            super(cls, self).__init__(*args, **kwargs)

        if cls.__init__ is cls.__base__.__init__ and cls is not NipypeProcess:
            # we must setup a conditional __init__ for each specialized class
            cls.__init__ = init_with_skip
            init_with_skip.cls = cls

        # determine if we were called from within nipype_factory()
        stack = traceback.extract_stack()
        # stack[-1] should be here
        # stack[-2] may be nipype_factory
        if len(stack) >= 2:
            s2 = stack[-2]
            if s2[2] == 'nipype_factory':
                instance = super(NipypeProcess, cls).__new__(cls, *args,
                                                            **kwargs)
                setattr(instance, '__%s_np_init_done__' % cls.__name__, False)
                return instance
        nipype_class = getattr(cls, '_nipype_class_type', None)
        nargs = args
        nkwargs = kwargs
        arg0 = None
        if nipype_class is not None:
            arg0 = nipype_class()
        else:
            if 'nipype_class' in kwargs:
                arg0 = kwargs['nipype_class']()
                nkwargs = {k: v for k, v in kwargs if k != 'nipype_class'}
            elif 'nipype_instance' in kwargs:
                pass
            elif len(args) != 0:
                import nipype.interfaces.base
                if isinstance(args[0], nipype.interfaces.base.BaseInterface):
                    arg0 = args[0]
                    nargs = nargs[1:]
                elif issubclass(args[0], nipype.interfaces.base.BaseInterface):
                    arg0 = args[0]()
                    nargs = args[1:]
        if arg0 is not None:
            from .nipype_process import nipype_factory
            instance = nipype_factory(arg0, base_class=cls, *nargs, **nkwargs)
            if cls != NipypeProcess:
                # override direct nipype reference
                instance.id = instance.__class__.__module__ + "." \
                    + instance.name
            instance.__postinit__(*nargs, **nkwargs)
        else:
            instance = super(NipypeProcess, cls).__new__(cls, *args, **kwargs)
            setattr(instance, '__%s_np_init_done__' % cls.__name__, False)
        return instance


    def __init__(self, nipype_instance=None, use_temp_output_dir=None,
                 *args, **kwargs):
        """ Initialize the NipypeProcess class.

        NipypeProcess instance gets automatically an additional user trait
        'output_directory'.

        This class also fix also some lacks of the nipye version '0.10.0'.

        NipypeProcess is normally not instantiated directly, but through the
        CapsulEngine factory, using a nipype interface name::

            ce = capsul_engine()
            npproc = ce.get_process_instance('nipype.interfaces.spm.Smooth')

        However it is now still possible to instantiate it directly, using a
        nipype interface class or instance::

            npproc = NipypeProcess(nipype.interfaces.spm.Smooth)

        NipypeProcess may be subclassed for specialized interfaces. In such a
        case, the subclass may provide:

        * (optionally) a class attribute `_nipype_class_type` to specify the
        nipype interface class. If present the nipype interface class or
        instance will not be specified in the constructor call.
        * (optionally) a :meth:`__postinit__` method which will be called in
        addition to the constructor, but later once the instance is correctly
        setup. This `__postinit__` method allows to customize the new class
        instance.
        * (optionally) a class attribute `_nipype_trait_mapping`: a dict
        specifying a translation table between nipype traits names and the
        names they will get in the Process instance. By default inputs get the
        same name as in their nipype interface, and outputs are prefixed with
        an underscore ('_') to avoid names collisions when a trait exists both
        in inputs and outputs in nipype. A special trait name
        `_spm_script_file` is also used in SPM interfaces to write the matlab
        script. It can also be translated to a different name in this dict.

        Subclasses should preferably *not* define an __init__ method, because
        it may be called twice if no precaution is taken to avoid it (a
        `__np_init_done__` instance attribute is set once init is done the
        first time).

        Ex::

            class Smooth(NipypeProcess):
                _nipype_class_type = spm.Smooth
                _nipype_trait_mapping = {
                    'smoothed_files': 'smoothed_files',
                    '_spm_script_file': 'spm_script_file'}

            smooth = Smooth()

        Parameters
        ----------
        nipype_instance: nipype interface (mandatory, except from internals)
            the nipype interface we want to wrap in capsul.
        use_temp_output_dir: bool or None
            use a temp working directory during processing

        Attributes
        ----------
        _nipype_interface : Interface
            private attribute to store the nipye interface
        _nipype_module : str
            private attribute to store the nipye module name
        _nipype_class : str
            private attribute to store the nipye class name
        _nipype_interface_name : str
            private attribute to store the nipye interface name
        """
        if hasattr(self, '__NipypeProcess_np_init_done__') \
                and self.__NipypeProcess_np_init_done__:
            # may be called twice, from within __new__ or from python internals
            return

        self.__NipypeProcess_np_init_done__ = True
        #super(NipypeProcess, self).__init__(*args, **kwargs)

        # Set some class attributes that characterize the nipype interface
        if nipype_instance is None:
            # probably called from a specialized subclass
            np_class = getattr(self, '_nipype_class_type', None)
            if np_class:
                nipype_instance = np_class()
            else:
                raise TypeError(
                    'NipypeProcess.__init__ must either be called with a '
                    'nipye interface instance as 1st argument, or from a '
                    'specialized subclass providing the _nipype_class_type '
                    'class attribute')
        self._nipype_interface = nipype_instance
        self._nipype_module = nipype_instance.__class__.__module__
        self._nipype_class = nipype_instance.__class__.__name__
        msplit = self._nipype_module.split(".")
        if len(msplit) > 2:
            self._nipype_interface_name = msplit[2]
        else:
            self._nipype_interface_name = 'custom'

        # Inheritance: activate input files copy for spm interfaces.
        if self._nipype_interface_name == "spm":
            # Copy only 'copyfile' nipype traits
            inputs_to_copy = list(self._nipype_interface.inputs.traits(
                copyfile=True).keys())
            inputs_to_symlink = list(self._nipype_interface.inputs.traits(
                copyfile=False).keys())
            out_traits = self._nipype_interface.output_spec().traits()
            inputs_to_clean = [x for x in inputs_to_copy
                               if 'modified_%s' % x not in out_traits]
            if use_temp_output_dir is None:
                use_temp_output_dir = True
            super(NipypeProcess, self).__init__(
                activate_copy=True, inputs_to_copy=inputs_to_copy,
                inputs_to_symlink=inputs_to_symlink,
                inputs_to_clean=inputs_to_clean,
                use_temp_output_dir=use_temp_output_dir,
                *args, **kwargs)
        else:
            if use_temp_output_dir is None:
                use_temp_output_dir = False
            super(NipypeProcess, self).__init__(
                  activate_copy=False, use_temp_output_dir=use_temp_output_dir,
                  *args, **kwargs)

        # Replace the process name and identification attributes
        self.id = ".".join([self._nipype_module, self._nipype_class])
        self.name = self._nipype_interface.__class__.__name__

        # Add a new trait to store the processing output directory
        super(Process, self).add_trait(
            "output_directory", directory(undefined, exists=True,
                                          optional=True))

        # Add a 'synchronize' nipype input trait that will be used to trigger
        # manually the output nipype/capsul traits sync.
        super(Process, self).add_field("synchronize", int, default=0,
                                       optional=True)

        # use the nipype doc for help
        doc = getattr(nipype_instance, '__doc__')
        if doc:
            self.__doc__ = doc


    def __postinit__(self, *args, **kwargs):
        '''
        `__postinit__` allows to customize subclasses. the base `NipypeProcess`
        implementation does nothing, it is empty.
        '''
        pass


    def requirements(self):
        req = {'nipype': 'any'}
        if self._nipype_interface_name == "spm":
            req['spm'] = 'any'
        elif self._nipype_interface_name == "fsl":
            req['fsl'] = 'any'
        elif self._nipype_interface_name == "freesurfer":
            req['freesurfer'] = 'any'
        return req


    def set_output_directory(self, out_dir):
        """ Set the process output directory.

        Parameters
        ----------
        out_dir: str (mandatory)
            the output directory
        """
        self.output_directory = out_dir

    def set_usedefault(self, parameter, value):
        """ Set the value of the usedefault attribute on a given parameter.

        Parameters
        ----------
        parameter: str (mandatory)
            name of the parameter to modify.
        value: bool (mandatory)
            value set to the usedefault attribute
        """
        setattr(self._nipype_interface.inputs, parameter, value)

    def _before_run_process(self):
        if self._nipype_interface_name == "spm":
            # Set the spm working
            self.destination = None
        super()._before_run_process()
        # configure nipype from config env variables (which should have been set
        # before now)
        from capsul.in_context import nipype as inp_npp
        inp_npp.configure_all()

    def run(self):
        """ Method that do the processings when the instance is called.

        Returns
        -------
        runtime: InterfaceResult
            object containing the running results
        """
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = None
        if self.output_directory is None or self.output_directory is undefined:
            raise ValueError('output_directory is not set but is mandatory '
                             'to run a NipypeProcess')
        os.chdir(self.output_directory)
        self.synchronize += 1

        # Force nipype update
        for trait_name in self._nipype_interface.inputs.traits().keys():
            if trait_name in self.user_traits():
                old = getattr(self._nipype_interface.inputs, trait_name)
                new = getattr(self, trait_name)
                if old is undefined and old != new:
                    setattr(self._nipype_interface.inputs, trait_name, new)

        results = self._nipype_interface.run()
        self.synchronize += 1

        # For spm, need to move the batch
        # (create in cwd: cf nipype.interfaces.matlab.matlab l.181)
        if self._nipype_interface_name == "spm":
            mfile = os.path.join(
                os.getcwd(),
                self._nipype_interface.mlab.inputs.script_file)
            destmfile = os.path.join(
                self.output_directory,
                self._nipype_interface.mlab.inputs.script_file)
            if os.path.isfile(mfile):
                shutil.move(mfile, destmfile)

        # Restore cwd
        if cwd is not None:
            os.chdir(cwd)

        #return results.__dict__
        return None

    def _after_run_process(self, run_process_result):
        trait_map = getattr(self, '_nipype_trait_mapping', {})
        script_tname = trait_map.get('_spm_script_file', '_spm_script_file')
        if getattr(self, script_tname, None) not in (None, undefined, ''):
            script_file = os.path.join(
                self.output_directory,
                self._nipype_interface.mlab.inputs.script_file)
            if os.path.exists(script_file):
                shutil.move(script_file, getattr(self, script_tname))
        return super(NipypeProcess,
                     self)._after_run_process(run_process_result)
