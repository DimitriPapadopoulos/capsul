# -*- coding: utf-8 -*-
'''
Specialized Node subclasses for CAPSUL pipeline elements

Classes
=======
:class:`Switch`
---------------
'''

import typing

from soma.controller import (Controller, field,
                             undefined, Literal, List, type_from_str)

from ..process.node import Plug, Node



class Switch(Node):
    """ Switch node to select a specific Process.

    A switch commutes a group of inputs to its outputs, according to its
    "switch" attribute value. Each group may be typically linked to a different
    process. Processes not "selected" by the switch are disabled, if possible.
    Values are also propagated through inputs/outputs of the switch
    (see below).

    Inputs / outputs:

    Say the switch "my_switch" has 2 outputs, "param1" and "param2". It will
    be connected to the outputs of 2 processing nodes, "node1" and "node2",
    both having 2 outputs: node1.out1, node1.out2, node2.out1, node2.out2.
    The switch will thus have 4 entries, in 2 groups, named for instance
    "node1" and "node2". The switch will link the outputs of node1 or
    node2 to its outputs. The switch inputs will be named as follows:

    * 1st group: "node1_switch_param1", "node1_switch_param2"
    * 2nd group: "node2_switch_param1", "node2_switch_param2"

    * When my_switch.switch value is "node1", my_switch.node1_switch_param1
      is connected to my_switch.param1 and my_switch.node1_switch_param2 is
      connected to my_switch.param2. The processing node node2 is disabled
      (unselected).
    * When my_switch.switch value is "node2", my_switch.node2_switch_param1
      is connected to my_switch.param1 and my_switch.node2_switch_param2 is
      connected to my_switch.param2. The processing node node1 is disabled
      (unselected).

    Values propagation:

    * When a switch is activated (its switch parameter is changed), the
      outputs will reflect the selected inputs, which means their values will
      be the same as the corresponding inputs.

    * But in many cases, parameters values will be given from the output
      (if the switch output is one of the pipeline outputs, this one will be
      visible from the "outside world, not the switch inputs). In this case,
      values set as a switch input propagate to its inputs.

    * An exception is when a switch input is linked to the parent pipeline
      inputs: its value is also visible from "outside" and should not be set
      via output values via the switch. In this specific case, output values
      are not propagated to such inputs.

    Notes
    -----
    Switch is normally not instantiated directly, but from a pipeline
    :py:meth:`pipeline_definition
    <capsul.pipeline.pipeline.Pipeline.pipeline_definition>` method

    Attributes
    ----------
    _switch_values : list
        the switch options
    _outputs: list
        the switch output parameters

    See Also
    --------
    capsul.pipeline.pipeline.Pipeline.add_switch
    capsul.pipeline.pipeline.Pipeline.pipeline_definition
    """

    _doc_path = 'api/pipeline.html#capsul.pipeline.pipeline_nodes.Switch'

    def __init__(self, pipeline, name, inputs, outputs, make_optional=(),
                 output_types=None):
        """ Generate a Switch Node

        Warnings
        --------
        The input plug names are built according to the following rule:
        <input_name>_switch_<output_name>

        Parameters
        ----------
        pipeline: Pipeline (mandatory)
            the pipeline object where the node is added
        name: str (mandatory)
            the switch node name
        inputs: list (mandatory)
            a list of options
        outputs: list (mandatory)
            a list of output parameters
        make_optional: sequence (optional)
            list of optional outputs.
            These outputs will be made optional in the switch output. By
            default they are mandatory.
        output_types: sequence of types (optional)
            If given, this sequence should have the same size as outputs. It
            will specify each switch output parameter type. 
            Input parameters for each input block will also have this
            type.
        """
        # if the user pass a simple element, create a list and add this
        # element
        self.__block_output_propagation = False
        if not isinstance(outputs, list):
            outputs = [outputs, ]
        if output_types is not None:
            if not isinstance(output_types, list) \
                    and not isinstance(output_types, tuple):
                raise ValueError(
                    'output_types parameter should be a list or tuple')
            if len(output_types) != len(outputs):
                raise ValueError('output_types should have the same number of '
                                 'elements as outputs')
        else:
            output_types = [typing.Any] * len(outputs)

        # check consistency
        if not isinstance(inputs, list) or not isinstance(outputs, list):
            raise Exception("The Switch node input and output parameters "
                            "are inconsistent: expect list, "
                            "got {0}, {1}".format(type(inputs), type(outputs)))

        # private copy of outputs and inputs
        self._outputs = outputs
        self._switch_values = inputs

        # format inputs and outputs to inherit from Node class
        flat_inputs = []
        for switch_name in inputs:
            flat_inputs.extend(["{0}_switch_{1}".format(switch_name, plug_name)
                                for plug_name in outputs])
        node_inputs = ([dict(name="switch"), ] +
                       [dict(name=i, optional=True) for i in flat_inputs])
        node_outputs = [dict(name=i, optional=(i in make_optional))
                        for i in outputs]
        # inherit from Node class
        super().__init__('capsul.pipeline.pipeline_nodes.Switch', pipeline,
                         name)

        # add switch enum attribute to select the process
        kwargs = {}
        if len(inputs) != 0:
            kwargs['default'] = inputs[0]
        self.add_field("switch", Literal[tuple(inputs)], **kwargs)

        # add a attribute for each input and each output
        input_types = output_types * len(inputs)
        for ni, type_ in zip(node_inputs[1:], input_types):
            i = ni['name']
            optional = ni['optional']
            self.add_field(i, type_, metadata={
                'output': False,
                'optional': optional
            })
            if 'write' in self.field(i).metadata():
                # fix output state for write items
                del self.field(i).write
                self.plugs[i].output = False
        for ni, type_ in zip(node_outputs, output_types):
            i = ni['name']
            optional = ni['optional']
            self.add_field(i, type_, metadata={
                'output': True,
                'optional': optional
            })

        for node in node_inputs[1:]:
            plug = self.plugs[node["name"]]
            plug.enabled = False

        self.on_attribute_change.add(self._any_attribute_changed)
        self.on_attribute_change.add(self._switch_changed, 'switch')

        # activate the switch first Process
        if self._switch_values:
            self._switch_changed(self._switch_values[0], undefined)

    def _switch_changed(self, new_selection, old_selection):
        """ Add an event to the switch attribute that enables us to select
        the desired option.

        Parameters
        ----------
        old_selection: str (mandatory)
            the old option
        new_selection: str (mandatory)
            the new option
        """
        if new_selection is undefined:
            return
        self.__block_output_propagation = True
        self.pipeline.delay_update_nodes_and_plugs_activation()
        # deactivate the plugs associated with the old option
        if old_selection is undefined:
            old_plug_names = []
        else:
            old_plug_names = [f'{old_selection}_switch_{plug_name}'
                              for plug_name in self._outputs]
        for plug_name in old_plug_names:
            self.plugs[plug_name].enabled = False

        # activate the plugs associated with the new option
        if new_selection is undefined:
            new_plug_names = []
        else:
            new_plug_names = [f'{new_selection}_switch_{plug_name}'
                              for plug_name in self._outputs]
        for plug_name in new_plug_names:
            self.plugs[plug_name].enabled = True

        # refresh the pipeline
        self.pipeline.update_nodes_and_plugs_activation()

        # Refresh the links to the output plugs
        for output_plug_name in self._outputs:
            # Get the associated input name
            corresponding_input_plug_name = f'{new_selection}_switch_{output_plug_name}'

            # Update the output value
            setattr(self, output_plug_name,
                    getattr(self, corresponding_input_plug_name, undefined))

            # Propagate the associated field documentation
            out_field = self.field(output_plug_name)
            in_field = self.field(corresponding_input_plug_name)
            out_field.doc = in_field.metadata('doc', None)

        self.pipeline.restore_update_nodes_and_plugs_activation()
        self.__block_output_propagation = False

    def connections(self):
        """ Returns the current internal connections between input and output
        plugs

        Returns
        -------
        connections: list
            list of internal connections
            [(input_plug_name, output_plug_name), ...]
        """
        return [(f'{self.switch}_switch_{plug_name}', plug_name)
                for plug_name in self._outputs]

    def _any_attribute_changed(self, new, old, name):
        """Callback linked to the switch attribute modification that enables
        the selection the desired option.

        Propagates value through the switch, from in put to output if the
        switch state corresponds to this input, or from output to inputs.

        Parameters
        ----------
        name: str (mandatory)
            the attribute name
        old: str (mandatory)
            the old value
        new: str (mandatory)
            the new value
        """
        from .pipeline import Pipeline

        # if the value change is on an output of the switch, and comes from
        # an "external" assignment (ie not the result of switch action or
        # change in one of its inputs), then propagate the new value to
        # all corresponding inputs.
        # However those inputs which are connected to a pipeline input are
        # not propagated, to avoid cyclic feedback between outputs and inputs
        # inside a pipeline
        if hasattr(self, '_outputs') and not self.__block_output_propagation \
                and name in self._outputs:
            self.__block_output_propagation = True
            flat_inputs = [f'{switch_name}_switch_{name}'
                           for switch_name in self._switch_values]
            for input_name in flat_inputs:
                # check if input is connected to a pipeline input
                plug = self.plugs[input_name]
                for link_spec in plug.links_from:
                    if isinstance(link_spec[2], Pipeline) \
                            and not link_spec[3].output:
                        break
                else:
                    setattr(self, input_name, new)
            self.__block_output_propagation = False
        # if the change is in an input, change the corresponding output
        # accordingly, if the current switch selection is on this input.
        spliter = name.split("_switch_")
        if len(spliter) == 2 and spliter[0] in self._switch_values:
            switch_selection, output_plug_name = spliter
            if self.switch == switch_selection:
                self.__block_output_propagation = True
                setattr(self, output_plug_name, new)
                self.__block_output_propagation = False

    def __setstate__(self, state):
        self.__block_output_propagation = True
        super(Switch, self).__setstate__(state)

    def get_connections_through(self, plug_name, single=False):
        if not self.activated or not self.enabled:
            return []
        plug = self.plugs[plug_name]
        if plug.output:
            connected_plug_name = '%s_switch_%s' % (self.switch, plug_name)
        else:
            splitter = plug_name.split("_switch_")
            if len(splitter) != 2:
                # not a switch input plug
                return []
            connected_plug_name = splitter[1]
        connected_plug = self.plugs[connected_plug_name]
        if plug.output:
            links = connected_plug.links_from
        else:
            links = connected_plug.links_to
        dest_plugs = []
        for link in links:
            if link[2] is self.pipeline.pipeline_node:
                other_end = [(link[2], link[1], link[3])]
            else:
                other_end = link[2].get_connections_through(link[1], single)
            dest_plugs += other_end
            if other_end and single:
                break
        return dest_plugs

    def is_job(self):
        return False

    def get_switch_inputs(self):
        inputs = []
        for field in self.fields():  # noqa: F402
            if field.is_output():
                continue
            plug = self.plugs[field.name]
            ps = plug.split('_switch_')
            if len(ps) == 2 and self.field(ps[1]) is not None \
                    and self.field(ps[1]).is_output():
                inputs.append(ps[0])
        return inputs

    @classmethod
    def configure_controller(cls):
        c = Controller()
        c.add_field('inputs', List[str])
        c.add_field('outputs', List[str])
        c.add_field('optional_params', List[str], default_factory=lambda: [])
        c.add_field('output_types', List[str])
        c.inputs = ['input_1', 'input_2']
        c.outputs = ['output']
        c.output_types = ['Any']
        return c

    def configured_controller(self):
        c = self.configure_controller()
        c.outputs = [field.name for field in self.fields()  # noqa: F811
                     if field.is_output()]
        c.inputs = self.get_switch_inputs()
        c.output_types = [self.field(p).type_str()
                          for p in self.outputs]
        c.optional_params = [self.field(p).optional for p in self.inputs]
        return c

    @classmethod
    def build_node(cls, pipeline, name, conf_controller):
        node = Switch(pipeline, name, conf_controller.inputs,
                      conf_controller.outputs,
                      make_optional=conf_controller.optional_params,
                      output_types=[type_from_str(x) for x in conf_controller.output_types])
        return node


class SwitchFactory:
    def __init__(self, pipeline, switch_name, switch_value):
        self.pipeline = pipeline
        self.switch = pipeline.add_switch(switch_name, [], [],
                                          export_switch=False,
                                          switch_value=switch_value)

    def add_option(self, option_name, /, make_optional=None, **kwargs):
        self.switch._switch_values.append(option_name)
        default = self.switch.field('switch').default_value()
        current = self.switch.switch
        self.switch.remove_field('switch')
        self.switch.add_field('switch', 
                              Literal[tuple(self.switch._switch_values)],
                              default=default)
        if current is not undefined:
            setattr(self.switch, 'switch', current)
        for output_name, input_node_and_plug in kwargs.items():
            l = input_node_and_plug.split('.')
            if len(l) != 2:
                raise ValueError(f'Invalid node parameter: {input_node_and_plug}')
            node_name, plug_name = l
            node = self.pipeline.nodes.get(node_name)
            if not node:
                raise ValueError(f'Unknown node: {node_name}')
            plug = node.plugs.get(plug_name)
            if plug is None:
                raise ValueError(f'Unknown parameter for node {node_name}: {plug_name}')
            input_field = node.field(plug_name)
            output_field = self.switch.field(output_name)
            
            if not output_field:
                optional = input_field.optional
                if make_optional and output_name in make_optional:
                    optional = True
                kwargs = dict((k, getattr(input_field,k)) for k in ('read', 'write', 'allowed_extensions', 'doc') if getattr(input_field, k, undefined) is not undefined)
                self.switch.add_field(output_name,
                                      type_=input_field.type,
                                      output=True,
                                      optional=optional,
                                      **kwargs)
                if self.switch.switch != output_name:
                    self.switch.plugs[output_name].enabled = False
                self.switch._outputs.append(output_name)
            input_name = f'{option_name}_switch_{output_name}'
            self.switch.add_field(input_name,
                                  type_=input_field.type,
                                  output=False)
            if self.switch.switch != output_name:
                self.switch.plugs[input_name].enabled = False
            self.pipeline.add_link(f'{input_node_and_plug}->{self.switch.name}.{option_name}_switch_{output_name}')
        
        if len(self.switch._switch_values) == 1:
            self.switch.switch = self.switch._switch_values[0]
