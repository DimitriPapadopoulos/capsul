from __future__ import print_function

import unittest
from capsul.api import Process, Pipeline, StudyConfig
from capsul.pipeline import pipeline_workflow
from capsul.pipeline import python_export
from capsul.pipeline import xml
import traits.api as traits
import os
import tempfile
import sys
import shutil
import json


class TestProcess(Process):
    def __init__(self):
        super(TestProcess, self).__init__()
        self.add_trait('in1', traits.File(output=False))
        self.add_trait('model', traits.File(output=False))
        self.add_trait('out1', traits.File(output=True))

    def _run_process(self):
        print('in1:', self.in1)
        print('out1:', self.out1)
        with open(self.out1, 'w') as f:
            print('test: %s' % os.path.basename(self.out1), file=f)
            print('##############', file=f)
            with open(self.in1, 'r') as ff:
                f.write(ff.read())
            print('model: %s' % os.path.basename(self.model), file=f)
            print('##############', file=f)
            with open(self.model, 'r') as ff:
                f.write(ff.read())
        # TODO FIXME: this should be automatic
        output_dict = {'out1': self.out1}
        return output_dict


class TrainProcess1(Process):
    def __init__(self):
        super(TrainProcess1, self).__init__()
        self.add_trait('in1', traits.List(traits.File(), output=False))
        self.add_trait('out1', traits.File(output=True))

    def _run_process(self):
        with open(self.out1, 'w') as of:
            for fname in self.in1:
                print('train1. File: %s' % os.path.basename(fname), file=of)
                print('--------------------', file=of)
                with open(fname) as f:
                    of.write(f.read())


class TrainProcess2(Process):
    def __init__(self):
        super(TrainProcess2, self).__init__()
        self.add_trait('in1', traits.List(traits.File(), output=False))
        self.add_trait('in2', traits.File(output=False))
        self.add_trait('out1', traits.File(output=True))

    def _run_process(self):
        with open(self.out1, 'w') as of:
            for fname in self.in1:
                print('train2, in1. File: %s' % os.path.basename(fname),
                      file=of)
                print('===================', file=of)
                with open(fname) as f:
                    of.write(f.read())
            print('train2, in2. File: %s' % os.path.basename(fname), file=of)
            print('====================', file=of)
            with open(self.in2) as f:
                of.write(f.read())

class CatFileProcess(Process):
    def __init__(self):
        super(CatFileProcess, self).__init__()
        self.add_trait('files', traits.List(traits.File(), output=False))
        self.add_trait('output', traits.File(output=True))

    def _run_process(self):
        with open(self.output, 'w') as of:
            for fname in self.files:
                with open(fname) as f:
                    of.write(f.read())

class Pipeline1(Pipeline):
    def pipeline_definition(self):
        self.add_process('train1', TrainProcess1())
        self.add_process('train2', TrainProcess2())

        self.add_custom_node('LOO',
                             'capsul.pipeline.custom_nodes.loo_node',
                             parameters={'test_is_output': False,
                                         'has_index': False})
        self.nodes['LOO'].activation_mode = 'by test'
        self.add_custom_node(
            'output_file',
            'capsul.pipeline.custom_nodes.strcat_node.StrCatNode',
            parameters={'parameters': ['base', 'separator', 'subject'],
                        'concat_plug': 'out_file',
                        'param_types': ['Directory', 'Str',
                                        'Str'],
                        'outputs': ['base'],
            },
            make_optional=['subject', 'separator'])
        self.nodes['output_file'].subject = 'output_file'
        self.nodes['output_file'].separator = os.path.sep

        self.add_custom_node(
            'intermediate_output',
            'capsul.pipeline.custom_nodes.strcat_node.StrCatNode',
            parameters={'parameters': ['base', 'sep',
                                      'subject', 'suffix'],
                        'concat_plug': 'out_file',
                        'outputs': ['base'],
                        'param_types': ['Directory', 'Str',
                                        'Str', 'Str', 'File']
                        },
            make_optional=['subject', 'sep', 'suffix'])
        self.nodes['intermediate_output'].sep = os.sep
        self.nodes['intermediate_output'].subject = 'output_file'
        self.nodes['intermediate_output'].suffix = '_interm'

        self.add_process('test', TestProcess())
        self.nodes['test'].process.trait('out1').input_filename = False

        self.add_custom_node(
            'test_output',
            'capsul.pipeline.custom_nodes.strcat_node.StrCatNode',
            parameters={'parameters': ['base', 'sep',
                                      'subject', 'suffix'],
                        'concat_plug': 'out_file',
                        'outputs': ['base'],
                        'param_types': ['Directory', 'Str',
                                        'Str', 'Str', 'File']
                        },
            make_optional=['subject', 'sep', 'suffix'])
        self.nodes['test_output'].sep = os.path.sep
        self.nodes['test_output'].subject = 'output_file'
        self.nodes['test_output'].suffix = '_test_output'

        self.export_parameter('LOO', 'inputs', 'main_inputs')
        self.export_parameter('LOO', 'test', 'test')
        self.export_parameter('output_file', 'base', 'output_directory')
        self.export_parameter('output_file', 'subject')
        self.export_parameter('test', 'out1', 'test_output', is_optional=True)
        self.add_link('LOO.train->train1.in1')
        self.add_link('main_inputs->train2.in1')
        self.add_link('train1.out1->train2.in2')
        self.add_link('train1.out1->intermediate_output.out_file')
        self.add_link('intermediate_output.base->output_directory')
        self.add_link('subject->intermediate_output.subject')
        self.add_link('train2.out1->output_file.out_file')
        self.add_link('test->test.in1')
        self.add_link('train2.out1->test.model')
        self.add_link('test.out1->test_output.out_file')
        self.add_link('test_output.base->output_directory')
        self.add_link('subject->test_output.subject')

        #self.do_not_export = set([('train2', 'out1'),
                                  #('intermediate_output', 'base'),
                                  #('intermediate_output', 'suffix'),
                                  #('intermediate_output', 'out_file')])

        self.node_position = {
            'LOO': (157.165005, 137.83779999999996),
            'inputs': (-58.0, 198.49999999999994),
            'intermediate_output': (380.19948, 19.431299999999965),
            'output_file': (588.66203, 106.0),
            'outputs': (922.7516925, 160.97519999999992),
            'test': (577.7010925, 328.2691),
            'test_output': (700.5415049999999, 242.76909999999995),
            'train1': (277.82609249999996, 152.83779999999996),
            'train2': (429.6447925, 249.58780000000002)}

class PipelineLOO(Pipeline):
    def pipeline_definition(self):
        self.add_iterative_process(
            'train', 'capsul.pipeline.test.test_custom_nodes.Pipeline1',
            iterative_plugs=['test',
                            'subject',
                            'test_output'])
            #do_not_export=['test_output'])
        self.add_process(
            'global_output',
            'capsul.pipeline.test.test_custom_nodes.CatFileProcess')
        self.export_parameter('train', 'main_inputs')
        self.export_parameter('train', 'subject', 'subjects')
        self.export_parameter('train', 'output_directory')
        #self.export_parameter('train', 'test_output')
        self.export_parameter('global_output', 'output', 'test_output')
        self.add_link('main_inputs->train.test')
        self.add_link('train.test_output->global_output.files')
        self.pipeline_node.plugs['subjects'].optional = False

        self.node_position = {
            'global_output': (416.6660345018389, 82.62713792979389),
            'inputs': (-56.46187758535915, 33.76663793099311),
            'outputs': (567.2173021071882, 10.355615517551513),
            'train': (139.93023967435616, 5.012399999999985)}

class TestCustomNodes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix='swf_custom')
        self.temp_files = [self.temp_dir]
        os.mkdir(os.path.join(self.temp_dir, 'out_dir'))
        lines = [
            ['water', 'snow', 'vapor', 'ice'],
            ['stone', 'mud', 'earth'],
            ['wind', 'storm', 'air'],
            ['fire', 'flame'],
        ]
        for i in range(4):
            fline = lines.pop(0)
            with open(os.path.join(self.temp_dir, 'file%d' % i), 'w') as f:
                f.write('file%d:\n++++++\n' % i)
                for l, line in enumerate(fline):
                    f.write('line%d: %s\n' % (l, line))

    def tearDown(self):
        if '--keep-temp' not in sys.argv[1:]:
            for f in self.temp_files:
                if os.path.isdir(f):
                    try:
                        shutil.rmtree(f)
                    except:
                        pass
                else:
                    try:
                        os.unlink(f)
                    except:
                        pass
            self.temp_files = []
        else:
            print('Files not removed in %s' % self.temp_dir)

    def _test_custom_nodes(self, pipeline):
        pipeline.main_inputs = [os.path.join(self.temp_dir, 'file%d' % i)
                                for i in range(4)]
        pipeline.test = pipeline.main_inputs[2]
        pipeline.subject = 'subject2'
        pipeline.output_directory = os.path.join(self.temp_dir, 'out_dir')
        self.assertEqual(pipeline.nodes['train1'].process.out1,
                         os.path.join(pipeline.output_directory,
                                      '%s_interm' % pipeline.subject))
        self.assertEqual(pipeline.nodes['train2'].process.out1,
                         os.path.join(pipeline.output_directory,
                                      pipeline.subject))
        self.assertEqual(pipeline.nodes['test'].process.out1,
                         os.path.join(pipeline.output_directory,
                                      '%s_test_output' % pipeline.subject))
        out_trait_type \
            = pipeline.nodes['test_output'].trait('out_file').trait_type
        self.assertTrue(isinstance(out_trait_type, traits.File))

    def test_custom_nodes(self):
        sc = StudyConfig()
        pipeline = sc.get_process_instance(Pipeline1)
        self._test_custom_nodes(pipeline)

    def test_custom_nodes_workflow(self):
        sc = StudyConfig()
        pipeline = sc.get_process_instance(Pipeline1)
        pipeline.main_input = os.path.join(self.temp_dir, 'file')
        pipeline.output_directory = os.path.join(self.temp_dir, 'out_dir')
        wf = pipeline_workflow.workflow_from_pipeline(pipeline,
                                                      create_directories=False)
        self.assertEqual(len(wf.jobs), 7)
        self.assertEqual(len(wf.dependencies), 6)
        self.assertEqual(
            sorted([[x.name for x in d] for d in wf.dependencies]),
            sorted([['LOO', 'train1'], ['train1', 'train2'],
                    ['train1', 'intermediate_output'], ['train2', 'test'],
                    ['train2', 'output_file'], ['test', 'test_output']]))

    def _test_loo_pipeline(self, pipeline2):
        pipeline2.main_inputs = [os.path.join(self.temp_dir, 'file%d' % i)
                                 for i in range(4)]
        pipeline2.subjects = ['subject%d' % i for i in range(4)]
        pipeline2.output_directory = os.path.join(self.temp_dir, 'out_dir')
        pipeline2.test_output = os.path.join(self.temp_dir, 'out_dir',
                                             'outputs')
        wf = pipeline_workflow.workflow_from_pipeline(pipeline2,
                                                      create_directories=False)
        import soma_workflow.client as swc
        swc.Helper.serialize(os.path.join(self.temp_dir,
                                          'custom_nodes.workflow'), wf)
        import six
        #print('workflow:')
        #print('jobs:', wf.jobs)
        print('dependencies:', sorted([(x[0].name, x[1].name) for x in wf.dependencies]))
        #print('dependencies:', wf.dependencies)
        #print('links:', {n.name: {p: (l[0].name, l[1]) for p, l in six.iteritems(links)} for n, links in six.iteritems(wf.param_links)})
        self.assertEqual(len(wf.jobs), 31)
        self.assertEqual(len(wf.dependencies), 16*4 + 1)
        deps = sorted([['Pipeline1_map', 'LOO'],
                       ['Pipeline1_map', 'intermediate_output'],
                       ['Pipeline1_map', 'train2'],
                       ['Pipeline1_map', 'output_file'],
                       ['Pipeline1_map', 'test'],
                       ['Pipeline1_map', 'test_output'],
                       ['LOO', 'train1'],
                       ['train1', 'train2'], ['train1', 'intermediate_output'],
                       ['train2', 'test'], ['train2', 'output_file'],
                       ['test', 'test_output'],
                       ['intermediate_output', 'Pipeline1_reduce'],
                       ['output_file', 'Pipeline1_reduce'],
                       ['test_output', 'Pipeline1_reduce'],
                       ['test', 'Pipeline1_reduce']] * 4
                      + [['Pipeline1_reduce', 'global_output']])
        self.assertEqual(
            sorted([[x.name for x in d] for d in wf.dependencies]),
            deps)
        train1_jobs = [job for job in wf.jobs if job.name == 'train1']
        self.assertEqual(
            sorted([job.param_dict['out1'] for job in train1_jobs]),
            [os.path.join(pipeline2.output_directory, 'subject%d_interm' % i)
             for i in range(4)])
        train2_jobs = [job for job in wf.jobs if job.name == 'train2']
        self.assertEqual(
            sorted([job.param_dict['out1'] for job in train2_jobs]),
            [os.path.join(pipeline2.output_directory, 'subject%d' % i)
             for i in range(4)])
        test_jobs = [job for job in wf.jobs if job.name == 'test']
        self.assertEqual(
            sorted([job.param_dict['out1'] for job in test_jobs]),
            [os.path.join(pipeline2.output_directory,
                          'subject%d_test_output' % i)
             for i in range(4)])

    def test_leave_one_out_pipeline(self):
        sc = StudyConfig()
        pipeline = sc.get_process_instance(PipelineLOO)
        self._test_loo_pipeline(pipeline)

    def test_custom_nodes_py_io(self):
        sc = StudyConfig()
        pipeline = sc.get_process_instance(Pipeline1)
        py_file = tempfile.mkstemp(suffix='_capsul.py')
        pyfname = py_file[1]
        os.close(py_file[0])
        self.temp_files.append(pyfname)
        python_export.save_py_pipeline(pipeline, pyfname)
        pipeline2 = sc.get_process_instance(pyfname)
        self._test_custom_nodes(pipeline)

    def test_custom_nodes_xml_io(self):
        sc = StudyConfig()
        pipeline = sc.get_process_instance(Pipeline1)
        xml_file = tempfile.mkstemp(suffix='_capsul.xml')
        xmlfname = xml_file[1]
        os.close(xml_file[0])
        self.temp_files.append(xmlfname)
        xml.save_xml_pipeline(pipeline, xmlfname)
        pipeline2 = sc.get_process_instance(xmlfname)
        self._test_custom_nodes(pipeline2)

    def test_loo_py_io(self):
        sc = StudyConfig()
        pipeline = sc.get_process_instance(PipelineLOO)
        py_file = tempfile.mkstemp(suffix='_capsul.py')
        pyfname = py_file[1]
        os.close(py_file[0])
        self.temp_files.append(pyfname)
        python_export.save_py_pipeline(pipeline, pyfname)
        pipeline2 = sc.get_process_instance(pyfname)
        self._test_loo_pipeline(pipeline2)

    def test_loo_xml_io(self):
        sc = StudyConfig()
        pipeline = sc.get_process_instance(PipelineLOO)
        xml_file = tempfile.mkstemp(suffix='_capsul.xml')
        xmlfname = xml_file[1]
        os.close(xml_file[0])
        self.temp_files.append(xmlfname)
        xml.save_xml_pipeline(pipeline, xmlfname)
        pipeline2 = sc.get_process_instance(xmlfname)
        self._test_loo_pipeline(pipeline2)


def test():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCustomNodes)
    runtime = unittest.TextTestRunner(verbosity=2).run(suite)
    return runtime.wasSuccessful()


if __name__ == '__main__':
    print("RETURNCODE: ", test())

    if '-v' in sys.argv[1:] or '--verbose' in sys.argv[1:]:
        import sys
        from soma.qt_gui.qt_backend import QtGui
        from capsul.qt_gui.widgets import PipelineDevelopperView

        app = QtGui.QApplication.instance()
        if not app:
            app = QtGui.QApplication(sys.argv)
        #pipeline = Pipeline1()
        #pipeline.main_inputs = [os.path.join(self.temp_dir, 'file%d' % i
        #for i in range(4)])
        #pipeline.test = pipeline.main_inputs[2]
        #pipeline.subject = 'subject2'
        #pipeline.output_directory = os.path.join(self.temp_dir, 'out_dir')
        #view1 = PipelineDevelopperView(pipeline, allow_open_controller=True,
                                       #show_sub_pipelines=True,
                                       #enable_edition=True)
        #view1.show()

        pipeline2 = PipelineLOO()
        pipeline2.main_inputs = ['/tmp/file%d' % i for i in range(4)]
        pipeline2.test = pipeline2.main_inputs[2]
        pipeline2.subjects = ['subject%d' % i for i in range(4)]
        pipeline2.output_directory = '/tmp/out_dir'
        #wf = pipeline_workflow.workflow_from_pipeline(pipeline2,
                                                      #create_directories=False)
        view2 = PipelineDevelopperView(pipeline2, allow_open_controller=True,
                                       show_sub_pipelines=True,
                                       enable_edition=True)
        view2.show()

        app.exec_()
        #del view1
        del view2


