"""
Metadata handling and attributes-based path generation system. In other words, this module is the completion system for Capsul processes and other executables.

The main function to be used contains most of the doc: see :func:`generate_paths`
"""

import csv
import fnmatch
import functools
import itertools
import json
import operator
from pathlib import Path
import re
import sys
import importlib
import weakref

from capsul.pipeline.pipeline import Process, Pipeline
from capsul.pipeline.process_iteration import ProcessIteration

from soma.controller import Controller, Literal, Directory
from soma.undefined import undefined

global_debug = False


class Dataset(Controller):
    """
    Dataset representation.
    You don't need to define or instantiate this class yourself, it will be done automatically and internally in the path generation system.

    Instead, users need to define datasets in the Capsul config. See :func:`generate_paths`.
    """

    path: Directory
    metadata_schema: str

    schemas = {}
    """
    Schemas mapping associating a :class:`MetadataSchema` class to a name
    """

    schema_mappings = {}
    """
    Mapping between schemas couples and conversion functions
    """

    def __init__(self, path=None, metadata_schema=None):
        super().__init__()
        self.on_attribute_change.add(self.schema_change_callback, "metadata_schema")
        if path is not None:
            if isinstance(path, Path):
                self.path = str(path)
            else:
                self.path = path
            if metadata_schema is None:
                capsul_json = Path(self.path) / "capsul.json"
                if capsul_json.exists():
                    with capsul_json.open() as f:
                        metadata_schema = json.load(f).get("metadata_schema")
        if metadata_schema:
            self.metadata_schema = metadata_schema

    @classmethod
    def find_schema(cls, metadata_schema):
        schema = cls.schemas.get(metadata_schema)
        if schema is None:
            try:
                # try to import the schema from capsul.schemas
                importlib.import_module(f"capsul.schemas.{metadata_schema}")
            except ImportError:
                pass  # oh well...
            schema = cls.schemas.get(metadata_schema)
        return schema

    @classmethod
    def find_schema_mapping(cls, source_schema, target_schema):
        return cls.schema_mappings.get((source_schema, target_schema))

    def find(self, **kwargs):
        yield from self.schema.find(**kwargs)

    def schema_change_callback(self):
        schema_cls = self.find_schema(self.metadata_schema)
        if not schema_cls:
            raise ValueError(
                f'Invalid metadata schema "{self.metadata_schema}" for path "{self.path}"'
            )
        self.schema = schema_cls(base_path=self.path)


class MetadataSchema(Controller):
    """Schema of metadata associated to a file in a :class:`Dataset`

    Abstract class: derived classes should overload the :meth:`_path_list`
    static method to actually implement path building.

    This class is a :class:`~soma.controller.controller.Controller`: attributes
    are stored as fields.
    """

    def __init_subclass__(cls) -> None:
        result = super().__init_subclass__()
        Dataset.schemas[cls.schema_name] = cls
        return result

    def __init__(self, base_path="", **kwargs):
        super().__init__(**kwargs)
        if (
            base_path is not undefined
            and base_path is not None
            and not isinstance(base_path, Path)
        ):
            self.base_path = Path(base_path)
        else:
            self.base_path = base_path

    def get(self, name, default=None):
        """
        Shortcut to get an attribute with a None default value.
        Used in :meth:`_path_list` specialization to have a
        shorter code.
        """
        return getattr(self, name, default)

    def build_path(self, unused_meta=None):
        """Returns a list of path elements built from the current PathLayout
        fields values.

        This method calls :meth:`_path_listh` which should be implemented in
        subclasses.
        """
        return functools.reduce(
            operator.truediv, self._path_list(unused_meta=unused_meta), self.base_path
        )

    def build_param(self, path_type=False, unused_meta=None):
        if path_type:
            return self.build_path(unused_meta=unused_meta)
        return "/".join(
            [
                str(p)
                for p in self._path_list(unused_meta=unused_meta)
                if p not in (None, undefined, "")
            ]
        )

    def _path_list(self, unused_meta=None):
        """Builds a path from metadata values in the fields of the current
        MetadataSchema fields. The returned path is a list of directories and
        ends with a filename. The resulting path is an
        ``os.path.join(path_list).``
        """

        raise NotImplementedError(
            "_path_list() must be specialized in MetadataSchema subclasses."
        )

    def metadata(self, path):
        """
        Parse the ``path`` argument to extract an attributes dict from it.

        The base method is not implemented, it must be reimplemented in subclasses that allow path -> attributes parsing. Not all schemas will allow it.
        """
        raise NotImplementedError(
            "metadata() must be specialized in MetadataSchema subclasses."
        )


class BIDSSchema(MetadataSchema):
    """Metadata schema for BIDS datasets"""

    schema_name = "bids"

    folder: Literal["sourcedata", "rawdata", "derivative"]
    process: str = None
    sub: str
    ses: str
    data_type: str = None
    task: str = None
    acq: str = None
    ce: str = None
    rec: str = None
    run: str = None
    echo: str = None
    part: str = None
    suffix: str = None
    extension: str

    path_pattern = re.compile(
        r"(?P<folder>[^-_/]*)/"
        r"sub-(?P<sub>[^-_/]*)/"
        r"ses-(?P<ses>[^-_/]*)/"
        r"(?P<data_type>[^/]*)/"
        r"sub-(?P=sub)_ses-(?P=ses)"
        r"(?:_task-(?P<task>[^-_/]*))?"
        r"(?:_acq-(?P<acq>[^-_/]*))?"
        r"(?:_ce-(?P<ce>[^-_/]*))?"
        r"(?:_rec-(?P<rec>[^-_/]*))?"
        r"(?:_run-(?P<run>[^-_/]*))?"
        r"(?:_echo-(?P<echo>[^-_/]*))?"
        r"(?:_part-(?P<part>[^-_/]*))?"
        r"(?:_(?P<suffix>[^-_/]*))?\.(?P<extension>.*)$"
    )

    def __init__(self, base_path="", **kwargs):
        super().__init__(base_path, **kwargs)

        # Cache of TSV files that are already read and converted
        # to a dictionary
        self._tsv_to_dict = {}

    def _path_list(self, unused_meta=None):
        """
        The path has one of the following pattern:
          {folder}/sub-{sub}/ses-{ses}/{data_type}/sub-{sub}_ses-{ses}[_task-{task}][_acq-{acq}][_ce-{ce}][_rec-{rec}][_run-{run}][_echo-{echo}][_part-{part}]{modality}.{extension}
          derivative/{process}/ses-{ses}/{data_type}/sub-{sub}_ses-{ses}[_task-{task}][_acq-{acq}][_ce-{ce}][_rec-{rec}][_run-{run}][_echo-{echo}][_part-{part}]{modality}.{extension}
        """
        path_list = [self.folder]
        if self.process:
            if not self.folder:
                self.folder = "derivative"
            elif self.folder != "derivative":
                raise ValueError(
                    'BIDS schema with a process requires folder=="derivative"'
                )
            path_list += [self.process]
        path_list += [f"sub-{self.sub}", f"ses-{self.ses}"]
        if self.data_type:
            path_list.append(self.data_type)
        elif not self.process:
            raise ValueError(
                "BIDS schema requires a value for either " "data_type or process"
            )

        filename = [f"sub-{self.sub}", f"ses-{self.ses}"]
        for key in ("task", "acq", "ce", "rec", "run", "echo", "part"):
            value = getattr(self, key, undefined)
            if value:
                filename.append(f"{key}-{value}")
        if self.suffix:
            filename.append(f"{self.suffix}.{self.extension}")
        else:
            filename[-1] += f".{self.extension}"
        path_list.append("_".join(filename))
        return path_list

    def tsv_to_dict(self, tsv):
        """
        Reads a TSV file and convert it to a dictionary of dictionaries
        using the first row value as key and other row values are converted
        to dict. The first row must contains column names.
        """
        result = self._tsv_to_dict.get(tsv)
        if result is None:
            result = {}
            with open(tsv) as f:
                reader = csv.reader(f, dialect="excel-tab")
                header = next(reader)[1:]
                for row in reader:
                    key = row[0]
                    value = dict(zip(header, row[1:]))
                    result[key] = value
            self._tsv_to_dict[tsv] = result
        return result

    def metadata(self, path):
        """
        Get metadata from BIDS files given its path.
        During the process, TSV files that are read and converted
        to dictionaries values are cached in self. That way if the same
        BIDSMetadata is used to get metadata of many files, there
        will be only one reading and conversion per file.
        """
        if not isinstance(path, Path):
            path = Path(path)
        result = {}
        if path.is_absolute():
            relative_path = path.relative_to(self.base_path)
        else:
            relative_path = path
            path = self.base_path / path
        m = self.path_pattern.match(str(relative_path))
        extsuffix = ""
        if m:
            if m.groupdict().get("extension") == "gz":
                m2 = self.path_pattern.match(str(relative_path)[:-3])
                if m2:
                    m = m2
                    extsuffix += ".gz"

            result.update((k, v) for k, v in m.groupdict().items() if v is not None)
        folder = result.get("folder")
        sub = result.get("sub")
        extension = result.get("extension")
        if extsuffix:
            if extension:
                extension += extsuffix
            else:
                extension = "gz"
            result["extension"] = extension
        if folder and sub:
            ses = result.get("ses")
            if ses:
                sessions_file = (
                    self.base_path / folder / f"sub-{sub}" / f"sub-{sub}_sessions.tsv"
                )
                if sessions_file.exists():
                    sessions_data = self.tsv_to_dict(sessions_file)
                    session_metadata = sessions_data.get(f"ses-{ses}", {})
                    result.update(session_metadata)
                scans_file = (
                    self.base_path
                    / folder
                    / f"sub-{sub}"
                    / f"ses-{ses}"
                    / f"sub-{sub}_ses-{ses}_scans.tsv"
                )
            else:
                scans_file = (
                    self.base_path / folder / f"sub-{sub}" / f"sub-{sub}_scans.tsv"
                )
            if scans_file.exists():
                scans_data = self.tsv_to_dict(scans_file)
                scan_metadata = scans_data.get(
                    str(path.relative_to(scans_file.parent)), {}
                )
                result.update(scan_metadata)
            extension = result.get("extension")
            if extension:
                json_path = path.parent / (path.name[: -len(extension) - 1] + ".json")
            else:
                json_path = path.parent / (path.name + ".json")
            if json_path.exists():
                with open(json_path) as f:
                    result.update(json.load(f))

        return result

    def find(self, **kwargs):
        """Find path from existing files given fixed values for some
        parameters (using :func:`glob.glob` and filenames parsing)

        Returns
        -------
        Yields a path for every match.
        """
        if "data_type" not in kwargs and "process" not in kwargs:
            layout = self.__class__(self.base_path, data_type="*", **kwargs)
        else:
            layout = self.__class__(self.base_path, **kwargs)
        for field in layout.fields():
            value = getattr(layout, field.name, undefined)
            if value is undefined:
                if not field.optional:
                    # Force the value of the attribute without
                    # using Pydantic validation because '*' may
                    # not be a valid value.
                    object.__setattr__(layout, field.name, "*")
        globs = layout._path_list()
        directories = [self.base_path]
        while len(globs) > 1:
            new_directories = []
            for d in directories:
                for sd in d.glob(globs[0]):
                    if sd.is_dir():
                        new_directories.append(sd)
            globs = globs[1:]
            directories = new_directories

        for d in directories:
            for sd in d.glob(globs[0]):
                yield sd


class BrainVISASchema(MetadataSchema):
    """Metadata schema for BrainVISA datasets."""

    schema_name = "brainvisa"

    center: str
    subject: str
    modality: str = None
    process: str = None
    analysis: str = "default_analysis"
    acquisition: str = "default_acquisition"
    preprocessings: str = None
    longitudinal: list[str] = None
    seg_directory: str = None
    sulci_graph_version: str = "3.1"
    sulci_recognition_session: str = "default_session"
    sulci_recognition_type: str = "auto"
    prefix: str = None
    short_prefix: str = None
    suffix: str = None
    extension: str = None
    side: str = None
    sidebis: str = None  # when used as a sufffix
    subject_in_filename: bool = True

    find_attrs = re.compile(
        r"(?P<folder>[^-_/]*)/"
        r"sub-(?P<sub>[^-_/]*)/"
        r"ses-(?P<ses>[^-_/]*)/"
        r"(?P<data_type>[^/]*)/"
        r"sub-(?P=sub)_ses-(?P=ses)"
        r"(?:_task-(?P<task>[^-_/]*))?"
        r"_(?P<suffix>[^-_/]*)\.(?P<extension>.*)$"
    )

    def _path_list(self, unused_meta=None):
        """
        The path has the following pattern:
        {center}/{subject}/[{modality}/][{process/][{acquisition}/][{preprocessings}/][{longitudinal}/][{analysis}/][seg_directory/][{sulci_graph_version}/[{sulci_recognition_session}]]/[side][{prefix}_][short_prefix]{subject}[_to_avg_{longitudinal}}[_{sidebis}{suffix}][.{extension}]
        """

        if unused_meta is None:
            unused_meta = set()
        elif not isinstance(unused_meta, set):
            unused_meta = set(unused_meta)

        path_list = []
        for key in (
            "center",
            "subject",
            "modality",
            "process",
            "acquisition",
            "preprocessings",
            "longitudinal",
            "analysis",
        ):
            if key not in unused_meta:
                value = getattr(self, key, None)
                if value:
                    path_list.append(value)
        if "seg_directory" not in unused_meta and self.seg_directory:
            path_list += self.seg_directory.split("/")
        if "sulci_graph_version" not in unused_meta and self.sulci_graph_version:
            path_list.append(self.sulci_graph_version)
            if (
                "sulci_recognition_session" not in unused_meta
                and self.sulci_recognition_session
            ):
                path_list.append(self.sulci_recognition_session)
                if (
                    "sulci_recognition_type" not in unused_meta
                    and self.sulci_recognition_type
                ):
                    path_list[-1] += f"_{self.sulci_recognition_type}"

        filename = []
        if "side" not in unused_meta and self.side:
            filename.append(f"{self.side}")
        if "prefix" not in unused_meta and self.prefix:
            filename.append(f"{self.prefix}_")
        if "short_prefix" not in unused_meta and self.short_prefix:
            filename.append(f"{self.short_prefix}")
        if "subject_in_filename" not in unused_meta and self.subject_in_filename:
            filename.append(self.subject)
        if "longitudinal" not in unused_meta and self.longitudinal:
            filename.append(f"_to_avg_{self.longitudinal}")
        if ("suffix" not in unused_meta and self.suffix) or (
            "sidebis" not in unused_meta and self.sidebis
        ):
            if filename:
                filename.append("_")
            if "sidebis" not in unused_meta and self.sidebis:
                filename.append(f"{self.sidebis}")
            if "sufffix" not in unused_meta and self.suffix:
                filename.append(f"{self.suffix}")
        if "extension" not in unused_meta and self.extension:
            filename.append(f".{self.extension}")
        path_list.append("".join(filename))

        return path_list


class MorphologistBIDSSchema(BrainVISASchema):
    schema_name = "morphologist_bids"

    folder: Literal["sourcedata", "rawdata", "derivative"]
    subject_only: bool = False

    def _path_list(self, unused_meta=None):
        if unused_meta is None:
            unused_meta = set()
        if "subject_only" not in unused_meta and self.subject_only:
            return [self.subject]

        if unused_meta is None:
            unused_meta = set()
        path_list = super()._path_list(unused_meta=unused_meta)
        pre_path = [f"sub-{self.subject}", f"ses-{self.acquisition}", "anat"]
        if "folder" not in unused_meta and self.folder not in (undefined, None, ""):
            pre_path.insert(0, self.folder)
        return pre_path + path_list[2:]


class SchemaMapping:
    def __init_subclass__(cls) -> None:
        Dataset.schema_mappings[(cls.source_schema, cls.dest_schema)] = cls


class BidsToBrainVISA(SchemaMapping):
    source_schema = "bids"
    dest_schema = "brainvisa"

    @staticmethod
    def map_schemas(source, dest):
        if dest.center is undefined:
            dest.center = "subjects"
        dest.subject = source.sub
        dest.acquisition = source.ses
        dest.extension = source.extension
        process = getattr(source, "process", None)
        if process:
            dest.process = process


class BidsToMorphoBids(SchemaMapping):
    source_schema = "bids"
    dest_schema = "morphologist_bids"

    @staticmethod
    def map_schemas(source, dest):
        BidsToBrainVISA.map_schemas(source, dest)


class process_schema:
    """Decorator used to register functions that defines how
    path parameters can be generated for an executable in the
    context of a dataset schema::

    from capsul.api import Process, process_schema
    from soma.controller import File, field

    class MyProcess(Process):
        input: field(type_=File, write=False)
        output: field(type_=File, write=True)

        def

    @process_schema('bids', MyProcess)
    def bids_MyProcess(executable, metadata):
        metadata.output.prefix.prepend("my_process")
    """

    modifier_function = {}

    def __init__(self, schema, executable):
        # Avoid circular import
        from capsul.application import Capsul

        self.schema = schema
        self.executable = Capsul.executable(executable).definition

    def __call__(self, function):
        self.modifier_function[(self.schema, self.executable)] = function
        return function


# class ProcessSchema:
#     """
#     Schema definition for a given process.

#     Needs to be subclassed as in, for instance::

#         from capsul.pipeline.test.fake_morphologist.normalization_t1_spm12_reinit \\
#             import normalization_t1_spm12_reinit

#         class SPM12NormalizationBIDS(ProcessSchema,
#                                      schema='bids',
#                                      process=normalization_t1_spm12_reinit):
#             output = {'part': 'normalized_spm12'}

#     This assigns metadata to a process parameters. Here in the example, the
#     parameter ``output`` of the process ``normalization_t1_spm12_reinit`` gets
#     a metadata dict ``{'part': 'normalized_spm12'}``.

#     The class does not need to be instantiated or registered anywhere: just its
#     declaration registers it automatically. Thus just importing the subclass
#     definition is enough.

#     Metadata can be assigned by parameter name, in a variable corresponding to
#     the parameter field::

#         output = {'part': 'normalized_spm12'}

#     or using wildcards. For this, a variable name cannot contain the character
#     ``*`` for instance. So we are using the special class variable ``_``, which
#     itself is a dict containing wildcard keys::

#         _ = {
#             '*': {'seg_directory': 'segmentation'},
#             '*_graph': {'extension': 'arg'}
#         }

#     Moreover in a pipeline, sub-nodes may be assigned metadata. This can be
#     given as a dict in the ``_nodes`` class variable::

#         _nodes = {
#             'LeftGreyWhiteClassification': {'*': {'side': 'L'}},
#             'RightGreyWhiteClassification': {'*': {'side': 'R'}},
#         }

#     Another special class variable may contain metadata links between the
#     process input and output parameters. This ``_meta_links`` variable is also
#     a dict (2 levels dict actually), and contains the list of metadata which
#     are propagated from a source parameter to a destination one inside the
#     given process (for the fiven schema)::

#         _meta_links = {
#             'histo_analysis': {
#                 'histo': ['prefix', 'side'],
#             }
#         }

#     in this example, the output parameter ``histo`` of the process will get its
#     ``prefix`` and ``side`` metadata from the input parameter
#     ``histo_analysis``.

#     By default, all metadata are systematically passed from inputs to outputs,
#     and merged in parameters order.

#     It is also possible to use wildcards in source and destination parameter
#     names, and the value may be an empty list (meaning that no metadata is
#     copied from this source to this destination)::

#     _meta_links = {
#             'histo_analysis': {
#                 '*': [],
#             }
#         }
#     """

#     def __init_subclass__(cls, schema, process) -> None:
#         from .application import get_node_class

#         super().__init_subclass__()
#         if isinstance(process, str):
#             process = get_node_class(process)[1]
#         if "metadata_schemas" not in process.__dict__:
#             process.metadata_schemas = {}
#         schemas = process.metadata_schemas
#         schemas[schema] = cls
#         cls.schema = schema
#         setattr(process, "metadata_schemas", schemas)


class MetadataModification:
    """Record a simple modification request of process metadata"""

    def __init__(self, executable):
        exported_parameters = {}
        for field in executable.user_fields():
            exported_parameters.setdefault(executable, {})[field.name] = field.name
            done = set()
            stack = [(executable, field.name)]
            while stack:
                node, parameter = stack.pop()
                done.add((node, parameter))
                plug = node.plugs[parameter]
                for (
                    dest_node_name,
                    dest_plug_name,
                    dest_node,
                    dest_plug,
                    weak_link,
                ) in itertools.chain(plug.links_from, plug.links_to):
                    if (dest_node, dest_plug_name) in done:
                        continue
                    stack.append((dest_node, dest_plug_name))
                if isinstance(node, Process) and node is not executable:
                    exported_parameters.setdefault(node, {})[parameter] = field.name
        super().__setattr__("_exported_parameters", exported_parameters)
        super().__setattr__("_parameter", None)
        super().__setattr__("_item", None)
        super().__setattr__("_actions", [])

    @property
    def executable(self):
        return self._executable()

    def set_executable(self, executable):
        super().__setattr__("_executable", weakref.ref(executable))

    def __getitem__(self, key):
        if self._parameter is None:
            super().__setattr__("_parameter", key)
        elif self._item is None:
            super().__setattr__("_item", key)
        else:
            raise Exception(
                "invalid metdata modification, attribute too deep: "
                f"{self._parameter}, {self._item}, {key}"
            )
        return self

    def __getattr__(self, attribute):
        return self[attribute]

    def __setitem__(self, key, value):
        if not self._parameter:
            raise Exception("invalid metdata modification, no parameter")
        if self._item:
            raise Exception(
                f"invalid metdata modification, unexpected item: {self._item}"
            )
        self._actions.append(
            functools.partial(
                self._apply_set,
                executable=self.executable,
                parameters=self._parameter,
                items=key,
                value=value,
            )
        )
        super().__setattr__("_parameter", None)

    def __setattr__(self, attribute, value):
        self[attribute] = value

    def _parameters(self, executable, parameters):
        if isinstance(parameters, str):
            selection = (re.compile(fnmatch.translate(parameters)),)
        else:
            selection = tuple(re.compile(fnmatch.translate(i)) for i in parameters)
        for field in executable.user_fields():
            if any(i.match(field.name) for i in selection):
                exported_name = self._exported_parameters.get(executable, {}).get(
                    field.name
                )
                if exported_name:
                    yield exported_name

    def _items(self, items):
        if isinstance(items, str):
            yield items
        else:
            yield from items

    def unused(self, value=True):
        if not self._parameter:
            raise Exception("invalid metdata modification, no parameter")
        if not self._item:
            raise Exception("invalid metdata modification, no item")

        self._actions.append(
            functools.partial(
                self._apply_unused,
                executable=self.executable,
                parameters=self._parameter,
                items=self._item,
                value=value,
            )
        )
        super().__setattr__("_parameter", None)
        super().__setattr__("_item", None)

    def append(self, value, sep="_"):
        if not self._parameter:
            raise Exception("invalid metdata modification, no parameter")
        if not self._item:
            raise Exception("invalid metdata modification, no item")
        self._actions.append(
            functools.partial(
                self._apply_append,
                executable=self.executable,
                parameters=self._parameter,
                items=self._item,
                value=value,
                sep=sep,
            )
        )
        super().__setattr__("_parameter", None)
        super().__setattr__("_item", None)

    def prepend(self, value, sep="_"):
        if not self._parameter:
            raise Exception("invalid metdata modification, no parameter")
        if not self._item:
            raise Exception("invalid metdata modification, no item")
        self._actions.append(
            functools.partial(
                self._apply_prepend,
                executable=self.executable,
                parameters=self._parameter,
                items=self._item,
                value=value,
                sep=sep,
            )
        )
        super().__setattr__("_parameter", None)
        super().__setattr__("_item", None)

    def _apply_set(self, unused, metadata, executable, parameters, items, value):
        for parameter in self._parameters(executable, parameters):
            for item in self._items(items):
                metadata.setdefault(parameter, {})[item] = value

    def _apply_unused(self, unused, metadata, executable, parameters, items, value):
        for parameter in self._parameters(executable, parameters):
            for item in self._items(items):
                unused.setdefault(parameter, {})[item] = value

    def _apply_append(
        self, unused, metadata, executable, parameters, items, value, sep
    ):
        for parameter in self._parameters(executable, parameters):
            for item in self._items(items):
                v = metadata.setdefault(parameter, {}).get(item)
                if v:
                    value = f"{v}{sep}{value}"
                metadata[parameter][item] = value

    def _apply_prepend(
        self, unused, metadata, executable, parameters, items, value, sep
    ):
        for parameter in self._parameters(executable, parameters):
            for item in self._items(items):
                v = metadata.setdefault(parameter, {}).get(item)
                if v:
                    value = f"{value}{sep}{v}"
                metadata[parameter][item] = value

    def _apply(self, unused, metadata):
        for action in self._actions:
            action(unused, metadata)


def resolve_process_schema(schema, executable):
    modification = MetadataModification(executable)

    _find_metadata_modification(modification, schema, executable)
    unused = {}
    metadata = {}
    modification._apply(unused, metadata)
    return (unused, metadata)


def _find_metadata_modification(modification, schema, executable):
    if isinstance(executable, Pipeline):
        for node in executable.nodes.values():
            if isinstance(node, Process) and node is not executable:
                _find_metadata_modification(modification, schema, node)
    modifier = process_schema.modifier_function.get((schema, executable.definition))
    if modifier:
        modification.set_executable(executable)
        modifier(modification)


def dprint(debug, *args, _frame_depth=1, **kwargs):
    if debug:
        import inspect

        frame = inspect.stack(context=0)[_frame_depth]
        try:
            head = f"!{frame.function}:{frame.lineno}!"
        finally:
            del frame
        print(head, " ".join(f"{i}" for i in args), file=sys.stderr, **kwargs)


def dpprint(debug, item):
    if debug:
        from pprint import pprint

        dprint(debug=debug, _frame_depth=2, file=sys.stderr)
        pprint(item)


class ProcessMetadata(Controller):
    def __init__(self, executable, execution_context, datasets=None, debug=False):
        super().__init__()
        self.executable = weakref.ref(executable)
        self.execution_context = execution_context
        self.datasets = datasets
        self._current_iteration = None
        self.debug = debug

        self.parameters_per_schema = {}
        self.schema_per_parameter = {}
        self.dataset_per_parameter = {}
        self.iterative_schemas = set()

        if isinstance(executable, ProcessIteration):
            process = executable.process
            iterative_parameters = executable.iterative_parameters
        else:
            iterative_parameters = set()
            process = executable

        # Associate each field to a dataset
        for field in process.user_fields():
            dataset_name = self.parameter_dataset_name(executable, field)
            if dataset_name is None:
                # no completion for this field
                continue
            dataset = getattr(execution_context.dataset, dataset_name, None)
            if dataset:
                self.dataset_per_parameter[field.name] = dataset_name
                schema = dataset.metadata_schema
                self.parameters_per_schema.setdefault(schema, []).append(field.name)
                self.schema_per_parameter[field.name] = schema
                if field.is_list() or field.name in iterative_parameters:
                    self.iterative_schemas.add(schema)

        for schema_name in self.parameters_per_schema:
            schema_cls = Dataset.find_schema(schema_name)
            if schema_cls is None:
                raise ValueError(f'Cannot find metadata schema named "{schema_name}"')
            if schema_name in self.iterative_schemas:
                self.add_field(schema_name, type_=list[schema_cls])
                setattr(self, schema_name, [])
            else:
                self.add_field(schema_name, type_=schema_cls)
                setattr(self, schema_name, schema_cls())

        if self.debug:
            self.dprint("Create ProcessMetadata for", self.executable().label)
            for field in process.user_fields():
                if field.path_type:
                    dataset = self.dataset_per_parameter.get(field.name)
                    schema = self.schema_per_parameter.get(field.name)
                    self.dprint(f"  {field.name} -> dataset={dataset}, schema={schema}")
            self.dprint("  Iterative schemas:", self.iterative_schemas)

    def dprint(self, *args, **kwargs):
        if isinstance(self.debug, bool) and self.debug:
            self.debug = sys.stderr
        if self.debug:
            print(*args, file=self.debug, **kwargs)

    def parameter_dataset_name(self, process, field):
        """
        Find the name of the dataset associated to a process parameter
        """
        dataset_name = None
        # 1: get manually given datasets
        if self.datasets and field.name in self.datasets:
            dataset_name = self.datasets[field.name]
            return dataset_name
        # Associates a Dataset name with the field
        if dataset_name is None:
            fmeta = field.metadata()
            if "dataset" in fmeta:
                dataset_name = getattr(field, "dataset", None)
                return dataset_name

        # not manually given: filter out non-path fields
        inner_field = None
        inner_field_name = None
        if isinstance(process, Pipeline):
            # inner_item = next(process.get_linked_items(
            #     process, field.name, direction=('links_from' if field.is_output() else 'links_to')), None)
            inner_item = next(
                process.get_linked_items(process, field.name, in_outer_pipelines=False),
                None,
            )
        else:
            inner_item = None
        if inner_item is not None:
            inner_process, inner_field_name = inner_item
            path_type = inner_process.field(inner_field_name).path_type
        else:
            path_type = field.path_type
        if path_type:
            # fallback 1: get in inner_field (of an inner process)
            if inner_field:
                dataset_name = getattr(inner_field, "dataset", None)
            # fallback 3: use "input" or "output"
            if dataset_name is None and (
                not self.datasets or field.name not in self.datasets
            ):
                dataset_name = "output" if field.is_output() else "input"
            return dataset_name
        return None

    def get_schema(self, schema_name, index=None):
        schema = getattr(self, schema_name)
        if isinstance(schema, list):
            if schema:
                if index is not None:
                    schema = schema[index]
                else:
                    schema = schema[self._current_iteration]
            else:
                schema = None
        return schema

    def generate_paths(self, executable=None):
        """
        Generate all paths for parameters of the given executable. Completion
        rules will apply using the current values of the metadata.
        """
        # self.debug = True
        if executable is None:
            executable = self.executable()

        if self.debug:
            if self._current_iteration is not None:
                iteration = f"[{self._current_iteration}]"
            else:
                iteration = ""
            self.dprint(f"Generate paths for {executable.name}{iteration}")
            for field in executable.user_fields():
                value = getattr(executable, field.name, undefined)
                self.dprint("       ", field.name, "=", repr(value))

        for parameter, value in self.path_for_parameters(executable).items():
            setattr(executable, parameter, value)

    def path_for_parameters(self, executable, parameters=None):
        """
        Generates a path (or value) for a given parameter.
        This is a restricted version of generate_paths(), which does not assign
        the generated value to the executable parameter but just returns it.
        """
        if executable is None:
            executable = self.executable()

        if isinstance(executable, ProcessIteration):
            raise NotImplementedError()
            # empty_iterative_schema = set()
            # iteration_size = 0
            # for schema in self.iterative_schemas:
            #     schema_iteration_size = len(getattr(self, schema))
            #     if schema_iteration_size:
            #         if iteration_size == 0:
            #             iteration_size = schema_iteration_size
            #             first_schema = schema
            #         elif iteration_size != schema_iteration_size:
            #             raise ValueError(
            #                 f"Iteration on schema {first_schema} has {iteration_size} element(s) which is not equal to schema {schema} ({schema_iteration_size} element(s))"
            #             )
            #     else:
            #         empty_iterative_schema.add(schema)

            # for schema in empty_iterative_schema:
            #     setattr(
            #         self,
            #         schema,
            #         [Dataset.find_schema(schema)() for i in range(iteration_size)],
            #     )

            # if parameter in executable.iterative_parameters:
            #     paths = []
            #     for iteration in range(iteration_size):
            #         self._current_iteration = iteration
            #         executable.select_iteration_index(iteration)
            #         path = self.parh_for_parameter(executable.process, parameter)
            #         paths.append(path)
            #         return paths
            # else:
            #     path = self.parh_for_parameter(executable.process, parameter)
            #     return path
        else:
            for schema_name in self.parameters_per_schema:
                for other_schema_name in self.parameters_per_schema:
                    if other_schema_name == schema_name:
                        continue
                    source = self.get_schema(schema_name)
                    dest = self.get_schema(other_schema_name)
                    if source and dest:
                        mapping = Dataset.find_schema_mapping(
                            schema_name, other_schema_name
                        )
                        if mapping:
                            mapping.map_schemas(source, dest)

            result = {}
            resolved_process_schemas = {}
            if parameters is None:
                parameters = (i.name for i in executable.user_fields())
            for parameter in parameters:
                schema = self.schema_per_parameter.get(parameter)
                if schema is None:
                    continue
                resolved_process_schema = resolved_process_schemas.get(schema)
                if resolved_process_schema is None:
                    resolved_process_schema = resolved_process_schemas[
                        schema
                    ] = resolve_process_schema(schema, executable)
                unused, metadata_values = resolved_process_schema
                dataset = self.dataset_per_parameter[parameter]
                metadata = Dataset.find_schema(schema)(
                    base_path=f"!{{dataset.{dataset}.path}}"
                )
                s = self.get_schema(schema)
                if s:
                    metadata.import_dict(s.asdict())
                metadata.import_dict(metadata_values)
                try:
                    path = str(
                        metadata.build_param(
                            executable.field(parameter).path_type,
                            unused_meta=unused,
                        )
                    )
                    result[parameter] = path
                except Exception:
                    pass
