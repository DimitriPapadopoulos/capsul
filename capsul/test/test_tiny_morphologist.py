# -*- coding: utf-8 -*-

import json
from pathlib import Path
import shutil
import tempfile
import unittest
import os.path as osp

from soma.controller import field, File
from soma.controller import Directory, undefined

from capsul.api import Capsul, Process, Pipeline
from capsul.config.configuration import ModuleConfiguration, default_engine_start_workers
from capsul.dataset import ProcessMetadata, ProcessSchema, Prepend, Append


class FakeSPMConfiguration(ModuleConfiguration):
    ''' SPM configuration module
    '''
    name = 'fakespm'
    directory: Directory
    version: str

    def __init__(self):
        super().__init__()

    def is_valid_config(self, requirements):
        required_version = requirements.get('version')
        if required_version \
                and getattr(self, 'version', undefined) != required_version:
            return False
        return True


class BiasCorrection(Process):
    input: field(type_=File, extensions=('.nii',))
    strength: float = 0.8
    output: field(type_=File, write=True, extensions=('.nii',))

    def execute(self, context):
        with open(self.input) as f:
            content = f.read()
        content = f'{content}Bias correction with strength={self.strength}\n'
        Path(self.output).parent.mkdir(parents=True, exist_ok=True)
        with open(self.output, 'w') as f:
            f.write(content)

class BiasCorrectionBIDS(ProcessSchema, schema='bids', process=BiasCorrection):
    output = Prepend('part', 'nobias')

class BiasCorrectionBrainVISA(ProcessSchema, schema='brainvisa', process=BiasCorrection):
    output = Prepend('prefix', 'nobias')


class FakeSPMNormalization12(Process):
    input: field(type_=File, extensions=('.nii',))
    template: field(
        type_=File, 
        extensions=('.nii',),
        completion='spm',
        dataset='fakespm'
    ) = '!{fakespm.directory}/template'
    output: field(type_=File, write=True, extensions=('.nii',))
    
    requirements = {
        'fakespm': {
            'version': '12'
        }
    }
    
    def execute(self, context):
        fakespmdir = Path(context.fakespm.directory)
        real_version = (fakespmdir / 'fakespm').read_text().strip()
        with open(self.input) as f:
            content = f.read()
        with open(self.template) as f:
            template = f.read().strip()
        content = f'{content}Normalization with fakespm {real_version} installed in {fakespmdir} using template "{template}"\n'
        with open(self.output, 'w') as f:
            f.write(content)

class FakeSPMNormalization12BIDS(ProcessSchema, schema='bids', process=FakeSPMNormalization12):
    output = Prepend('part', 'normalized_fakespm12')

class FakeSPMNormalization12BrainVISA(ProcessSchema, schema='brainvisa', process=FakeSPMNormalization12):
    output = Prepend('prefix', 'normalized_fakespm12')


class FakeSPMNormalization8(FakeSPMNormalization12):
    requirements = {
        'fakespm': {
            'version': '8'
        }
    }

class FakeSPMNormalization8BIDS(ProcessSchema, schema='bids', process=FakeSPMNormalization8):
    output = Prepend('part', 'normalized_fakespm8')

class FakeSPMNormalization8BrainVISA(ProcessSchema, schema='brainvisa', process=FakeSPMNormalization8):
    output = Prepend('prefix', 'normalized_fakespm8')


class AimsNormalization(Process):
    input: field(type_=File, extensions=('.nii',))
    origin: field(type_=list[float], default_factory=lambda: [1.2, 3.4, 5.6])
    output: field(type_=File, write=True, extensions=('.nii',))

    def execute(self, context):
        with open(self.input) as f:
            content = f.read()
        content = f'{content}Normalization with Aims, origin={self.origin}\n'
        Path(self.output).parent.mkdir(parents=True, exist_ok=True)
        with open(self.output, 'w') as f:
            f.write(content)

class AimsNormalizationBIDS(ProcessSchema, schema='bids', process=AimsNormalization):
    output = Prepend('part', 'normalized_aims')

class AimsNormalizationBrainVISA(ProcessSchema, schema='brainvisa', process=AimsNormalization):
    output = Prepend('prefix', 'normalized_aims')

class SplitBrain(Process):
    input: field(type_=File, extensions=('.nii',))
    right_output: field(type_=File, write=True, extensions=('.nii',))
    left_output: field(type_=File, write=True, extensions=('.nii',))

    def execute(self, context):
        with open(self.input) as f:
            content = f.read()
        for side in ('left', 'right'):
            side_content = f'{content}Split brain side={side}\n'
            output = getattr(self, f'{side}_output')
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            with open(output, 'w') as f:
                f.write(side_content)


class SplitBrainBrainVISA(ProcessSchema, schema='brainvisa', process=SplitBrain):
    _ = { 
        'right_output': Append('suffix', 'right'),
        'left_output': Append('suffix', 'left'),
    }
    right_output = Prepend('prefix', 'split')
    left_output = Prepend('prefix', 'split')


class ProcessHemisphere(Process):
    input: field(type_=File, extensions=('.nii',))
    output: field(type_=File, write=True, extensions=('.nii',))

    def execute(self, context):
        with open(self.input) as f:
            content = f.read()
        content = f'{content}Process hemisphere\n'
        with open(self.output, 'w') as f:
            f.write(content)


class ProcessHemisphereBrainVISA(ProcessSchema, schema='brainvisa', process=ProcessHemisphere):
    output = Prepend('prefix', 'hemi')


class TinyMorphologist(Pipeline):
    def pipeline_definition(self):
        self.add_process('nobias', BiasCorrection)

        self.add_process('fakespm_normalization_12', FakeSPMNormalization12)
        self.add_process('fakespm_normalization_8', FakeSPMNormalization8)
        self.add_process('aims_normalization', AimsNormalization)
        self.add_process('split', SplitBrain)
        self.add_process('right_hemi', ProcessHemisphere)
        self.add_process('left_hemi', ProcessHemisphere)
        self.create_switch('normalization', {
            'none': {'output': 'nobias.output'},
            'fakespm12': {'output': 'fakespm_normalization_12.output'},
            'fakespm8': {'output': 'fakespm_normalization_8.output'},
            'aims': {'output': 'aims_normalization.output'}
        })

        
        self.add_link('nobias.output->fakespm_normalization_12.input')
        self.add_link('nobias.output->fakespm_normalization_8.input')
        self.export_parameter('fakespm_normalization_12', 'template')
        self.add_link('template->fakespm_normalization_8.template')
        self.add_link('nobias.output->aims_normalization.input')

        self.export_parameter('nobias', 'output', 'nobias')

        self.add_link('normalization.output->split.input')
        self.export_parameter('normalization', 'output', 'normalized')
        self.add_link('split.right_output->right_hemi.input')
        self.export_parameter('right_hemi', 'output', 'right_hemisphere')
        self.add_link('split.left_output->left_hemi.input')
        self.export_parameter('left_hemi', 'output', 'left_hemisphere')

class TinyMorphologistBIDS(ProcessSchema, schema='bids', process=TinyMorphologist):
    _ = {
        '*': {'process': 'tinymorphologist'},
        'split.right_output': {'part': 'right_hemi'},
        'split.left_output': {'part': 'left_hemi'},
    }
    input = {'process': None}

class TinyMorphologistBrainVISA(ProcessSchema, schema='brainvisa', process=TinyMorphologist):
    _ = {
        '*': {'process': 'tinymorphologist'},
    }
    _nodes = {
        # 'split': {
        #     'right_output': {'prefix': 'right_hemi'},
        #     'left_output': {'prefix': 'left_hemi'}
        # },
        # 'fakespm_normalization_12': {'*': Append('suffix', 'fakespm12')},
        # 'fakespm_normalization_8': {'*': Append('suffix', 'fakespm8')},
        # 'aims_normalization': {'*': Append('suffix', 'aims')},
    }
    input = {'process': None}
    # left_hemisphere = {'prefix': 'left_hemi'}
    # right_hemisphere = {'prefix': 'right_hemi'}

def concatenate(inputs: list[File], result: File):
    with open(result, 'w') as o:
        for f in inputs:
            print('-' * 40, file=o)
            print(f, file=o)
            print('-' * 40, file=o)
            with open(f) as i:
                o.write(i.read())

class TestTinyMorphologist(unittest.TestCase):
    subjects = (
        'aleksander',
        'casimiro',
        # 'christophorus',
        # 'christy',
        # 'conchobhar',
        # 'cornelia',
        # 'dakila',
        # 'demosthenes',
        # 'devin',
        # 'ferit',
        # 'gautam',
        # 'hikmat',
        # 'isbel',
        # 'ivona',
        # 'jordana',
        # 'justyn',
        # 'katrina',
        # 'lyda',
        # 'melite',
        # 'til',
        # 'vanessza',
        # 'victoria'
    )

    def setUp(self):
        self.tmp = tmp = Path(tempfile.mkdtemp(prefix='capsul_test_'))
        #-------------------#
        # Environment setup #
        #-------------------#

        # Create BIDS directory
        self.bids = bids = tmp / 'bids'
        # Write Capsul specific information
        bids.mkdir()
        with (bids / 'capsul.json').open('w') as f:
            json.dump({
                'metadata_schema': 'bids'
            }, f)

        # Create BrainVISA directory
        self.brainvisa = brainvisa = tmp / 'brainvisa'
        brainvisa.mkdir()
        # Write Capsul specific information
        with (brainvisa / 'capsul.json').open('w') as f:
            json.dump({
                'metadata_schema': 'brainvisa'
            }, f)

        # Generate fake T1 and T2 data in bids directory
        for subject in self.subjects:
            for session in ('m0', 'm12', 'm24'):
                for data_type in ('T1w', 'T2w'):
                    subject_dir = bids/ f'rawdata' / f'sub-{subject}'
                    session_dir = subject_dir / f'ses-{session}'
                    file = session_dir / 'anat' / f'sub-{subject}_ses-{session}_{data_type}.nii'
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file_name = str(file.name)
                    with file.open('w') as f:
                        print(f'{data_type} acquisition for subject {subject} acquired in session {session}', file=f)
                    
                    sessions_file = subject_dir / f'sub-{subject}_sessions.tsv'
                    if not sessions_file.exists():
                        with open(sessions_file, 'w') as f:
                            f.write('session_id\tsession_metadata\n')
                    with open(sessions_file, 'a') as f:
                        f.write(f'ses-{session}\tsession metadata for {file_name}\n')

                    scans_file = session_dir / f'sub-{subject}_ses-{session}_scans.tsv'
                    if not scans_file.exists():
                        with open(scans_file, 'w') as f:
                            f.write('filename\tscan_metadata\n')
                    with open(scans_file, 'a') as f:
                        f.write(f'{file.relative_to(session_dir)}\tscan metadata for {file_name}\n')

                    with file.with_suffix('.json').open('w') as f:
                        json.dump(dict(
                            json_metadata=f'JSON metadata for {file_name}'
                        ),f)

        # Configuration base dictionary
        config = {
            'databases': {
                'builtin': {
                    'path': osp.join(tmp, 'capsul_engine_database.rdb'),
                },
            },
            'builtin': {
                'config_modules': [
                    'capsul.test.test_tiny_morphologist',
                ],
                'dataset': {
                    'input': {
                        'path': str(self.bids),
                        'metadata_schema': 'bids',
                    },
                    'output': {
                        'path': str(self.brainvisa),
                        'metadata_schema': 'brainvisa',
                    }
                }
            }
        }
        # Create fake SPM directories
        for version in ('8', '12'):
            fakespm = tmp / 'software' / f'fakespm-{version}'
            fakespm.mkdir(parents=True, exist_ok=True)
            # Write a file containing only the version string that will be used
            # by fakespm module to check installation.
            (fakespm / 'fakespm').write_text(version)
            # Write a fake template file
            (fakespm / 'template').write_text(f'template of fakespm {version}')
            fakespm_config = {
                'directory': str(fakespm),
                'version': version,
            }
            config['builtin'].setdefault('fakespm', {})[f'fakespm_{version}'] \
                = fakespm_config

        # Create a configuration file
        self.config_file = tmp / 'capsul_config.json'
        with self.config_file.open('w') as f:
            json.dump(config, f)

        self.capsul = Capsul('test_tiny_morphologist',
                             site_file=self.config_file, user_file=None)
        return super().setUp()

    def tearDown(self):
        self.capsul = None
        shutil.rmtree(self.tmp)
        return super().tearDown()

    def test_tiny_morphologist_config(self):
        self.maxDiff = 2000
        expected_config = {
            'databases': {
                'builtin': {
                    'path': osp.join(self.tmp, 'capsul_engine_database.rdb'),
                    'type': 'redis+socket'
                }
            },

            'builtin': {
                'database': 'builtin',
                'persistent': True,
                'start_workers': default_engine_start_workers,
                'dataset': {
                    'input': {
                        'path': str(self.tmp / 'bids'),
                        'metadata_schema': 'bids',
                    },
                    'output': {
                        'path': str(self.tmp / 'brainvisa'),
                        'metadata_schema': 'brainvisa',
                    },
                },
                'fakespm': {
                    'fakespm_12': {
                        'directory': str(self.tmp / 'software' / 'fakespm-12'),
                        'version': '12'
                    },
                    'fakespm_8': {
                        'directory': str(self.tmp / 'software' / 'fakespm-8'),
                        'version': '8'
                    }
                },
                'config_modules': ['capsul.test.test_tiny_morphologist'],
            }
        }
        self.assertEqual(self.capsul.config.asdict(), expected_config)

        engine = self.capsul.engine()
        tiny_morphologist = self.capsul.executable(
            'capsul.test.test_tiny_morphologist.TinyMorphologist')

        context = engine.execution_context(tiny_morphologist)
        expected_context = {
            'config_modules': ['capsul.test.test_tiny_morphologist'],
            'dataset': {
                'input': {
                    'path': str(self.tmp / 'bids'),
                    'metadata_schema': 'bids',
                },
                'output': {
                    'path': str(self.tmp / 'brainvisa'),
                    'metadata_schema': 'brainvisa',
                },
            },
        }
        dict_context = context.asdict()
        self.assertEqual(dict_context, expected_context)

        tiny_morphologist.normalization = 'fakespm12'
        context = engine.execution_context(tiny_morphologist)
        fakespm12_conf = {
            'directory': str(self.tmp / 'software' / 'fakespm-12'),
            'version': '12'
        }
        expected_context['fakespm'] = fakespm12_conf
        dict_context = context.asdict()
        self.assertEqual(dict_context, expected_context)

        tiny_morphologist_iteration = self.capsul.executable_iteration(
            'capsul.test.test_tiny_morphologist.TinyMorphologist',
            non_iterative_plugs=['template'],
        )

        context = engine.execution_context(tiny_morphologist_iteration)
        del expected_context['fakespm']
        context = engine.execution_context(tiny_morphologist_iteration)
        dict_context = context.asdict()
        self.assertEqual(dict_context, expected_context)
        tiny_morphologist_iteration.normalization = ['none', 'aims',
                                                     'fakespm12']
        expected_context['fakespm'] = fakespm12_conf
        context = engine.execution_context(tiny_morphologist_iteration)
        dict_context = context.asdict()
        self.assertEqual(dict_context, expected_context)

    def test_tiny_path_generation(self):
        expected = {
            'none': {
                'template': '!{fakespm.directory}/template',
                'nobias': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/nobias_aleksander.nii',
                'normalized': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/nobias_aleksander.nii',
                'right_hemisphere': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_nobias_aleksander_right.nii',
                'left_hemisphere': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_nobias_aleksander_left.nii',
            },
            'aims': {
                'template': '!{fakespm.directory}/template',
                'nobias': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/nobias_aleksander.nii',
                'normalized': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/normalized_aims_nobias_aleksander.nii',
                'right_hemisphere': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_aims_nobias_aleksander_right.nii',
                'left_hemisphere': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_aims_nobias_aleksander_left.nii',
            },
            'fakespm12': {
                'template': '!{fakespm.directory}/template',
                'nobias': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/nobias_aleksander.nii',
                'normalized': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/normalized_fakespm12_nobias_aleksander.nii',
                'right_hemisphere': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm12_nobias_aleksander_right.nii',
                'left_hemisphere': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm12_nobias_aleksander_left.nii',
            },
            'fakespm8': {
                'template': '!{fakespm.directory}/template',
                'nobias': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/nobias_aleksander.nii',
                'normalized': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/normalized_fakespm8_nobias_aleksander.nii',
                'right_hemisphere': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_right.nii',
                'left_hemisphere': '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_left.nii',
            },
        }

        tiny_morphologist = self.capsul.executable('capsul.test.test_tiny_morphologist.TinyMorphologist')            
        engine = self.capsul.engine()
        execution_context = engine.execution_context(tiny_morphologist)
        input = str(self.tmp / 'bids'/'rawdata'/'sub-aleksander'/'ses-m0'/'anat'/'sub-aleksander_ses-m0_T1w.nii')
        input_metadata = execution_context.dataset['input'].schema.metadata(input)
        self.assertEqual(input_metadata, {
            'folder': 'rawdata', 
            'sub': 'aleksander',
            'ses': 'm0',
            'data_type': 'anat',
            'suffix': 'T1w',
            'extension': 'nii',
            'session_metadata': 'session metadata for sub-aleksander_ses-m0_T2w.nii',
            'scan_metadata': 'scan metadata for sub-aleksander_ses-m0_T1w.nii',
            'json_metadata': 'JSON metadata for sub-aleksander_ses-m0_T1w.nii'})
        for normalization in ('none', 'aims', 'fakespm12', 'fakespm8'):
            tiny_morphologist.normalization = normalization
            metadata = ProcessMetadata(tiny_morphologist, execution_context)
            self.assertEqual(
                metadata.parameters_per_schema,
                {
                    'brainvisa': ['nobias', 'normalized', 'right_hemisphere', 'left_hemisphere'],
                    'bids': ['input']
                }
            )
            metadata.bids = input_metadata
            metadata.brainvisa = {'center': 'whatever'}
            self.assertEqual(
                metadata.bids.asdict(),
                {
                    'folder': 'rawdata',
                    'process': None, 
                    'sub': 'aleksander', 
                    'ses': 'm0', 
                    'data_type': 'anat', 
                    'task': None, 
                    'acq': None, 
                    'ce': None, 
                    'rec': None, 
                    'run': None, 
                    'echo': None, 
                    'part': None, 
                    'suffix': 'T1w', 
                    'extension': 'nii'
                })
            metadata.generate_paths(tiny_morphologist)
            params = dict((i, 
                getattr(tiny_morphologist, i, undefined)) for i in ('template', 
                    'nobias', 'normalized', 'right_hemisphere', 'left_hemisphere'))
            self.maxDiff = 2000
            self.assertEqual(params, expected[normalization])

            with self.capsul.engine() as engine:
                status = engine.run(tiny_morphologist, timeout=5)
                self.assertEqual(status, 'ended')


    def test_tiny_morphologist_iteration(self):
        expected_completion = {
            'input': [
                        '!{dataset.input.path}/rawdata/sub-aleksander/ses-m0/anat/sub-aleksander_ses-m0_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-aleksander/ses-m0/anat/sub-aleksander_ses-m0_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-aleksander/ses-m0/anat/sub-aleksander_ses-m0_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-aleksander/ses-m12/anat/sub-aleksander_ses-m12_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-aleksander/ses-m12/anat/sub-aleksander_ses-m12_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-aleksander/ses-m12/anat/sub-aleksander_ses-m12_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-aleksander/ses-m24/anat/sub-aleksander_ses-m24_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-aleksander/ses-m24/anat/sub-aleksander_ses-m24_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-aleksander/ses-m24/anat/sub-aleksander_ses-m24_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-casimiro/ses-m0/anat/sub-casimiro_ses-m0_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-casimiro/ses-m0/anat/sub-casimiro_ses-m0_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-casimiro/ses-m0/anat/sub-casimiro_ses-m0_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-casimiro/ses-m12/anat/sub-casimiro_ses-m12_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-casimiro/ses-m12/anat/sub-casimiro_ses-m12_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-casimiro/ses-m12/anat/sub-casimiro_ses-m12_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-casimiro/ses-m24/anat/sub-casimiro_ses-m24_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-casimiro/ses-m24/anat/sub-casimiro_ses-m24_T1w.nii',
                        '!{dataset.input.path}/rawdata/sub-casimiro/ses-m24/anat/sub-casimiro_ses-m24_T1w.nii',
            ],
            'left_hemisphere': [
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_nobias_aleksander_left.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_aims_nobias_aleksander_left.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_left.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_nobias_aleksander_left.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_normalized_aims_nobias_aleksander_left.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_left.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_nobias_aleksander_left.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_normalized_aims_nobias_aleksander_left.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_left.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_nobias_casimiro_left.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_normalized_aims_nobias_casimiro_left.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_left.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_nobias_casimiro_left.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_normalized_aims_nobias_casimiro_left.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_left.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_nobias_casimiro_left.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_normalized_aims_nobias_casimiro_left.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_left.nii',
            ],
            'nobias': [
                        '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/nobias_aleksander.nii',
                        '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/nobias_aleksander.nii',
                        '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/nobias_aleksander.nii',
                        '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m12/default_analysis/nobias_aleksander.nii',
                        '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m12/default_analysis/nobias_aleksander.nii',
                        '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m12/default_analysis/nobias_aleksander.nii',
                        '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m24/default_analysis/nobias_aleksander.nii',
                        '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m24/default_analysis/nobias_aleksander.nii',
                        '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m24/default_analysis/nobias_aleksander.nii',
                        '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m0/default_analysis/nobias_casimiro.nii',
                        '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m0/default_analysis/nobias_casimiro.nii',
                        '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m0/default_analysis/nobias_casimiro.nii',
                        '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m12/default_analysis/nobias_casimiro.nii',
                        '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m12/default_analysis/nobias_casimiro.nii',
                        '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m12/default_analysis/nobias_casimiro.nii',
                        '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m24/default_analysis/nobias_casimiro.nii',
                        '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m24/default_analysis/nobias_casimiro.nii',
                        '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m24/default_analysis/nobias_casimiro.nii',
            ],
            'normalization': ['none',
                                'aims',
                                'fakespm8',
                                'none',
                                'aims',
                                'fakespm8',
                                'none',
                                'aims',
                                'fakespm8',
                                'none',
                                'aims',
                                'fakespm8',
                                'none',
                                'aims',
                                'fakespm8',
                                'none',
                                'aims',
                                'fakespm8'],
            'right_hemisphere': [
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_nobias_aleksander_right.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_aims_nobias_aleksander_right.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_right.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_nobias_aleksander_right.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_normalized_aims_nobias_aleksander_right.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_right.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_nobias_aleksander_right.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_normalized_aims_nobias_aleksander_right.nii',
                                '!{dataset.output.path}/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_right.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_nobias_casimiro_right.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_normalized_aims_nobias_casimiro_right.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_right.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_nobias_casimiro_right.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_normalized_aims_nobias_casimiro_right.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_right.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_nobias_casimiro_right.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_normalized_aims_nobias_casimiro_right.nii',
                                '!{dataset.output.path}/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_right.nii',
            ],
        }

        expected_resolution = {
            'input': [
                        f'{self.tmp}/bids/rawdata/sub-aleksander/ses-m0/anat/sub-aleksander_ses-m0_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-aleksander/ses-m0/anat/sub-aleksander_ses-m0_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-aleksander/ses-m0/anat/sub-aleksander_ses-m0_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-aleksander/ses-m12/anat/sub-aleksander_ses-m12_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-aleksander/ses-m12/anat/sub-aleksander_ses-m12_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-aleksander/ses-m12/anat/sub-aleksander_ses-m12_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-aleksander/ses-m24/anat/sub-aleksander_ses-m24_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-aleksander/ses-m24/anat/sub-aleksander_ses-m24_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-aleksander/ses-m24/anat/sub-aleksander_ses-m24_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-casimiro/ses-m0/anat/sub-casimiro_ses-m0_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-casimiro/ses-m0/anat/sub-casimiro_ses-m0_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-casimiro/ses-m0/anat/sub-casimiro_ses-m0_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-casimiro/ses-m12/anat/sub-casimiro_ses-m12_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-casimiro/ses-m12/anat/sub-casimiro_ses-m12_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-casimiro/ses-m12/anat/sub-casimiro_ses-m12_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-casimiro/ses-m24/anat/sub-casimiro_ses-m24_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-casimiro/ses-m24/anat/sub-casimiro_ses-m24_T1w.nii',
                        f'{self.tmp}/bids/rawdata/sub-casimiro/ses-m24/anat/sub-casimiro_ses-m24_T1w.nii',
            ],
            'left_hemisphere': [
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_nobias_aleksander_left.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_aims_nobias_aleksander_left.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_left.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_nobias_aleksander_left.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_normalized_aims_nobias_aleksander_left.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_left.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_nobias_aleksander_left.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_normalized_aims_nobias_aleksander_left.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_left.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_nobias_casimiro_left.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_normalized_aims_nobias_casimiro_left.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_left.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_nobias_casimiro_left.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_normalized_aims_nobias_casimiro_left.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_left.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_nobias_casimiro_left.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_normalized_aims_nobias_casimiro_left.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_left.nii',
            ],
            'nobias': [
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m0/default_analysis/nobias_aleksander.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m0/default_analysis/nobias_aleksander.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m0/default_analysis/nobias_aleksander.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m12/default_analysis/nobias_aleksander.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m12/default_analysis/nobias_aleksander.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m12/default_analysis/nobias_aleksander.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m24/default_analysis/nobias_aleksander.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m24/default_analysis/nobias_aleksander.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m24/default_analysis/nobias_aleksander.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m0/default_analysis/nobias_casimiro.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m0/default_analysis/nobias_casimiro.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m0/default_analysis/nobias_casimiro.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m12/default_analysis/nobias_casimiro.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m12/default_analysis/nobias_casimiro.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m12/default_analysis/nobias_casimiro.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m24/default_analysis/nobias_casimiro.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m24/default_analysis/nobias_casimiro.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m24/default_analysis/nobias_casimiro.nii',
            ],
            'normalization': ['none',
                                'aims',
                                'fakespm8',
                                'none',
                                'aims',
                                'fakespm8',
                                'none',
                                'aims',
                                'fakespm8',
                                'none',
                                'aims',
                                'fakespm8',
                                'none',
                                'aims',
                                'fakespm8',
                                'none',
                                'aims',
                                'fakespm8'],
            'right_hemisphere': [
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_nobias_aleksander_right.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_aims_nobias_aleksander_right.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_right.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_nobias_aleksander_right.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_normalized_aims_nobias_aleksander_right.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m12/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_right.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_nobias_aleksander_right.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_normalized_aims_nobias_aleksander_right.nii',
                        f'{self.tmp}/brainvisa/whatever/aleksander/tinymorphologist/m24/default_analysis/hemi_split_normalized_fakespm8_nobias_aleksander_right.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_nobias_casimiro_right.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_normalized_aims_nobias_casimiro_right.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m0/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_right.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_nobias_casimiro_right.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_normalized_aims_nobias_casimiro_right.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m12/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_right.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_nobias_casimiro_right.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_normalized_aims_nobias_casimiro_right.nii',
                        f'{self.tmp}/brainvisa/whatever/casimiro/tinymorphologist/m24/default_analysis/hemi_split_normalized_fakespm8_nobias_casimiro_right.nii',
            ]
        }

        tiny_morphologist_iteration = self.capsul.executable_iteration(
            'capsul.test.test_tiny_morphologist.TinyMorphologist',
            non_iterative_plugs=['template'],
        )

        # class TinyMorphologistIterationBrainVISA(ProcessSchema, schema='brainvisa', process=tiny_morphologist_iteration):
        #     _ = {
        #         '*': {
        #             'suffix': lambda iteration_index, **kwargs: f'{{executable.normalization[{iteration_index}]}}',
        #         }
        #     }

        engine = self.capsul.engine()
        execution_context = engine.execution_context(
            tiny_morphologist_iteration)

        # Parse the dataset with BIDS-specific query (here "suffix" is part
        #  of BIDS specification). The object returned contains info for main
        # BIDS fields (sub, ses, acq, etc.)
        inputs = []
        normalizations = []
        for path in sorted(self.capsul.config.builtin.dataset.input.find(
                suffix='T1w', extension='nii')):
            input_metadata \
                = execution_context.dataset['input'].schema.metadata(path)
            inputs.extend([input_metadata]*3)
            normalizations += ['none', 'aims', 'fakespm8']
        tiny_morphologist_iteration.normalization = normalizations

        metadata = ProcessMetadata(tiny_morphologist_iteration,
                                   execution_context)
        metadata.bids = inputs
        metadata.brainvisa = [{'center': 'whatever'}] * len(inputs)
        metadata.generate_paths(tiny_morphologist_iteration)
        self.maxDiff = 11000
        for name, value in expected_completion.items():
            self.assertEqual(getattr(tiny_morphologist_iteration, name), value,
                             f'Differing value for parameter {name}')
        tiny_morphologist_iteration.resolve_paths(execution_context)
        for name, value in expected_resolution.items():
            self.assertEqual(getattr(tiny_morphologist_iteration, name), value,
                             f'Differing value for parameter {name}')

        with self.capsul.engine() as engine:
            status = engine.run(tiny_morphologist_iteration, timeout=10)

        self.assertEqual(
            status,
            'ended')


if __name__ == '__main__':
    import sys
    from soma.qt_gui.qt_backend import Qt
    from capsul.web import CapsulBrowserWindow

    qt_app = Qt.QApplication.instance()
    if not qt_app:
        qt_app = Qt.QApplication(sys.argv)
    self = TestTinyMorphologist()
    self.subjects = [f'subject{i:04}' for i in range(500)]
    print(f'Setting up config and data files for {len(self.subjects)} subjects and 3 time points')
    self.setUp()
    try:
        tiny_morphologist_iteration = self.capsul.executable_iteration(
            'capsul.test.test_tiny_morphologist.TinyMorphologist',
            non_iterative_plugs=['template'],
        )

        engine = self.capsul.engine()
        execution_context = engine.execution_context(tiny_morphologist_iteration)

        # Parse the dataset with BIDS-specific query (here "suffix" is part
        #  of BIDS specification). The object returned contains info for main
        # BIDS fields (sub, ses, acq, etc.)
        inputs = []
        normalizations = []
        for path in sorted(self.capsul.config.builtin.dataset.input.find(suffix='T1w', extension='nii')):
            input_metadata = execution_context.dataset['input'].schema.metadata(path)
            inputs.extend([input_metadata]*3)
            normalizations += ['none', 'aims', 'fakespm8']
        tiny_morphologist_iteration.normalization = normalizations

        
        metadata = ProcessMetadata(tiny_morphologist_iteration, execution_context)
        metadata.bids = inputs
        metadata.brainvisa = {'center': 'whatever'}
        metadata.generate_paths(tiny_morphologist_iteration)

        with self.capsul.engine() as engine:
            execution_id = engine.start(tiny_morphologist_iteration)
            try:
                widget = CapsulBrowserWindow()
                widget.show()
                from capsul.qt_gui.widgets import PipelineDeveloperView
                tiny_morphologist = Capsul.executable('capsul.test.test_tiny_morphologist.TinyMorphologist')
                # view1 = PipelineDeveloperView(tiny_morphologist, show_sub_pipelines=True, allow_open_controller=True, enable_edition=True)
                # view1.show()
                qt_app.exec_()
                del widget
                # del view1
                engine.wait(execution_id, timeout=1000)
                # engine.raise_for_status(execution_id)
            except TimeoutError:
                # engine.print_execution_report(engine.execution_report(engine.engine_id, execution_id))
                raise
    finally:
        self.tearDown()
