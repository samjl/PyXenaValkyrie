"""
Classes and utilities that represents Xena XenaManager-2G port.

:author: yoram@ignissoft.com
"""

import os
from collections import OrderedDict
from enum import Enum

from trafficgenerator.tgn_utils import TgnError

from xenavalkyrie.api.xena_socket import XenaCommandError
from xenavalkyrie.xena_object import XenaObject, XenaObject21
from xenavalkyrie.xena_stream import XenaStream, XenaStreamState


class XenaCaptureBufferType(Enum):
    raw = 0
    text = 1
    pcap = 2

class XenaPortPayloadModeEnum(Enum):
    NORMAL = 'NORMAL'#: normalmode
    EXTPL = 'EXTPL'#: extended payload
    CDF = 'CDF'#: customdatafield


class XenaPort(XenaObject):
    """ Represents Xena port. """

    cli_prefix = 'p'

    _info_config_commands = ['p_info', 'p_config', 'p_receivesync', 'ps_indices', 'pr_tplds']

    stats_captions = {'pr_pfcstats': ['total', 'CoS 0', 'CoS 1', 'CoS 2', 'CoS 3', 'CoS 4', 'CoS 5', 'CoS 6', 'CoS 7'],
                      'pr_total': ['bps', 'pps', 'bytes', 'packets'],
                      'pr_notpld': ['bps', 'pps', 'bytes', 'packets'],
                      'pr_extra': ['fcserrors', 'pauseframes', 'arprequests', 'arpreplies', 'pingrequests',
                                   'pingreplies', 'gapcount', 'gapduration'],
                      'pt_total': ['bps', 'pps', 'bytes', 'packets'],
                      'pt_extra': ['arprequests', 'arpreplies', 'pingrequests', 'pingreplies', 'injectedfcs',
                                   'injectedseq', 'injectedmis', 'injectedint', 'injectedtid', 'training'],
                      'pt_notpld': ['bps', 'pps', 'bytes', 'packets']}

    def __init__(self, parent, index):
        """ Create port object.

        Note that port can be child of chassis or module objects.

        :param parent: parent module or chassis.
        :param index: port index in format module/port (both 0 based)
        """

        if 'module' in parent.ref:
            objRef = '{}/port/{}'.format(parent.ref, index.split('/')[-1])
        else:
            objRef = '{}/module/{}/port/{}'.format(parent.ref, *index.split('/'))
        super(self.__class__, self).__init__(objType='port', index=index, parent=parent, objRef=objRef)
        self._data['name'] = '{}/{}'.format(parent.name, index)
        self.p_info = None

    def inventory(self):
        self.p_info = self.get_attributes()

    def reset(self):
        """ Reset port-level parameters to standard values, and delete all streams, filters, capture,
            and dataset definitions.
        """
        self.objects = OrderedDict()
        return self.send_command('p_reset')

    def wait_for_up(self, timeout=40):
        self.wait_for_states('p_receivesync', timeout, 'IN_SYNC')

    #
    # Configurations.
    #

    def load_config(self, config_file_name):
        """ Load configuration file from xpc file.

        :param config_file_name: full path to the configuration file.
        """

        with open(config_file_name) as f:
            commands = f.read().splitlines()

        for command in commands:
            if not command.startswith(';'):
                try:
                    self.send_command(command)
                except XenaCommandError as e:
                    self.logger.warning(str(e))

    def save_config(self, config_file_name, file_mode='w+'):
        """ Save configuration file to xpc file.

        :param config_file_name: full path to the configuration file.
        :param file_mode: w+ for port configuration file, a+ for module configuration.
        """

        with open(config_file_name, file_mode) as f:
            f.write(';Port: {}\n'.format(self.index))
            f.write('P_RESET\n')
            for line in self.send_command_return_multilines('p_fullconfig', '?'):
                f.write(line.split(' ', 1)[1].lstrip())

    def add_stream(self, name=None, tpld_id=None, state=XenaStreamState.enabled):
        """ Add stream.

        :param name: stream description.
        :param tpld_id: TPLD ID. If None the a unique value will be set.
        :param state: new stream state.
        :type state: xenavalkyrie.xena_stream.XenaStreamState
        :return: newly created stream.
        :rtype: xenavalkyrie.xena_stream.XenaStream
        """

        stream = XenaStream(parent=self, index='{}/{}'.format(self.index, len(self.streams)), name=name)
        stream._create()
        tpld_id = tpld_id if tpld_id else XenaStream.next_tpld_id
        stream.set_attributes(ps_comment='"{}"'.format(stream.name), ps_tpldid=tpld_id)
        XenaStream.next_tpld_id = max(XenaStream.next_tpld_id + 1, tpld_id + 1)
        stream.set_state(state)
        return stream

    def remove_stream(self, index):
        """ Remove stream.

        :param index: index of stream to remove.
        """

        self.streams[index].del_object_from_parent()

    #
    # Operations.
    #

    def start_traffic(self, blocking=False):
        """ Start port traffic.

        Port -> Start Traffic

        :param blocking: True - start traffic and wait until traffic ends, False - start traffic and return.
        """
        self.session.start_traffic(blocking, self)

    def stop_traffic(self):
        """ Stop port traffic.

        Port -> Stop Traffic
        """
        self.session.stop_traffic(self)

    def start_capture(self):
        """ Start capture on port.

        Capture -> Start Capture
        """
        self.del_objects_by_type('capture')
        self.send_command('p_capture', 'on')

    def stop_capture(self):
        """ Stop capture on port.

        Capture -> Stop Capture
        """
        self.send_command('p_capture', 'off')

    #
    # Statistics.
    #

    def clear_stats(self):
        """ Clear att TX and RX statistics counter.

        Port Statistics -> Clear TX Counters, Clear RX Counters
        """
        self.send_command('pt_clear')
        self.send_command('pr_clear')

    def read_port_stats(self):
        """
        :return: dictionary {group name {stat name: value}}.
            Sea XenaPort.stats_captions.
        """

        stats_with_captions = OrderedDict()
        for stat_name in self.stats_captions.keys():
            stats_with_captions[stat_name] = self.read_stat(self.stats_captions[stat_name], stat_name)
        return stats_with_captions

    def read_stream_stats(self):
        """
        :return: dictionary {stream index {stat name: value}}.
            Sea XenaStream.stats_captions.
        """
        stream_stats = OrderedDict()
        for stream in self.streams.values():
            stream_stats[stream] = stream.read_stats()
        return stream_stats

    def read_tpld_stats(self):
        """
        :return: dictionary {tpld index {group name {stat name: value}}}.
            Sea XenaTpld.stats_captions.
        """
        payloads_stats = OrderedDict()
        for tpld in self.tplds.values():
            payloads_stats[tpld] = tpld.read_stats()
        return payloads_stats


    def add_filter(self,conditions,state = True):
        fltr = XenaFilter(parent=self, index='{}/{}'.format(self.index, len(self.filters)))
        fltr._create()
        fltr.set_attributes(PF_CONDITION='{}'.format(conditions))
        fltr.set_state(state)
        return fltr

    def read_filter_stats(self):
        filter_stats = OrderedDict()
        for flt in self.filters.values():
            filter_stats[flt] = flt.read_stats()
        return filter_stats

    def clear_filters(self):
        self.send_command('PF_INDICES')
        for f in self.filters:
            self.filters[f].del_object_from_parent()

    #
    # Properties.
    #
    @property
    def filters(self):
        if not self.get_objects_by_type('xena_filter'):
            for index in self.get_attribute('PF_INDICES').split():
                fltr = XenaFilter(parent=self, index='{}/{}'.format(self.index, index, name=None))
                pf_comment = fltr.get_attribute('PF_COMMENT')
                if pf_comment:
                    fltr._data['name'] = pf_comment
        return {s.id: s for s in self.get_objects_by_type('xena_filter')}


    @property
    def streams(self):
        """
        :return: dictionary {id: object} of all streams.
        :rtype: dict of (int, xenavalkyrie.xena_stream.XenaStream)
        """
        if not self.get_objects_by_type('stream'):
            tpld_ids = []
            for index in self.get_attribute('ps_indices').split():
                stream = XenaStream(parent=self, index='{}/{}'.format(self.index, index, name=None))
                ps_comment = stream.get_attribute('ps_comment')
                if ps_comment:
                    stream._data['name'] = ps_comment
                tpld_ids.append(stream.get_attribute('ps_tpldid'))
            if tpld_ids:
                XenaStream.next_tpld_id = max([XenaStream.next_tpld_id] + [int(t) for t in tpld_ids]) + 1
        return {s.id: s for s in self.get_objects_by_type('stream')}

    @property
    def tplds(self):
        """
        :return: dictionary {id: object} of all current tplds.
        :rtype: dict of (int, xenavalkyrie.xena_port.XenaTpld)
        """

        # As TPLDs are dynamic we must re-read them each time from the port.
        self.parent.del_objects_by_type('tpld')
        for tpld in self.get_attribute('pr_tplds').split():
            XenaTpld(parent=self, index='{}/{}'.format(self.index, tpld))
        return {t.id: t for t in self.get_objects_by_type('tpld')}

    @property
    def capture(self):
        """
        :return: capture object.
        :rtype: XenaCapture
        """

        if not self.get_object_by_type('capture'):
            XenaCapture(parent=self)
        return self.get_object_by_type('capture')





class XenaTpld(XenaObject21):

    stats_captions = {'pr_tpldtraffic': ['bps', 'pps', 'byt', 'pac'],
                      'pr_tplderrors': ['dummy', 'seq', 'mis', 'pld'],
                      'pr_tpldlatency': ['min', 'avg', 'max', 'avg1sec', 'min1sec', 'max1sec'],
                      'pr_tpldjitter': ['min', 'avg', 'max', 'avg1sec', 'min1sec', 'max1sec']}

    def __init__(self, parent, index):
        """
        :param parent: parent port object.
        :param index: TPLD index in format module/port/tpld.
        """
        super(self.__class__, self).__init__(objType='tpld', index=index, parent=parent)

    def read_stats(self):
        """
        :return: dictionary {group name {stat name: value}}.
            Sea XenaTpld.stats_captions.
        """

        stats_with_captions = OrderedDict()
        for stat_name in self.stats_captions.keys():
            stats_with_captions[stat_name] = self.read_stat(self.stats_captions[stat_name], stat_name)
        return stats_with_captions


class XenaCapture(XenaObject):
    """ Represents capture parameters, correspond to the Capture panel of the XenaManager, and deal with configuration
        of the capture criteria and inspection of the captured data from a port.
    """

    _info_config_commands = ['pc_fullconfig']
    stats_captions = ['status', 'packets', 'starttime']

    def __init__(self, parent):
        objRef = '{}/capture'.format(parent.ref)
        super(self.__class__, self).__init__(objType='capture', index=parent.index, parent=parent, objRef=objRef)

    def read_stats(self):
        """
        :return: dictionary {stat name: value}.
            Sea XenaCapture.stats_captions.
        """
        return self.read_stat(XenaCapture.stats_captions, 'pc_stats')

    def get_packets(self, from_index=0, to_index=None, cap_type=XenaCaptureBufferType.text,
                    file_name=None, tshark=None):
        """ Get captured packets from chassis.

        :param from_index: index of first packet to read.
        :param to_index: index of last packet to read. If None - read all packets.
        :param cap_type: returned capture format. If pcap then file name and tshark must be provided.
        :param file_name: if specified, capture will be saved in file.
        :param tshark: tshark object for pcap type only.
        :type: xenavalkyrie.xena_tshark.Tshark
        :return: list of requested packets, None for pcap type.
        """

        to_index = to_index if to_index else len(self.packets)

        raw_packets = []
        for index in range(from_index, to_index):
            raw_packets.append(self.packets[index].get_attribute('pc_packet').split('0x')[1])

        if cap_type == XenaCaptureBufferType.raw:
            self._save_captue(file_name, raw_packets)
            return raw_packets

        text_packets = []
        for raw_packet in raw_packets:
            text_packet = ''
            for c, b in zip(range(len(raw_packet)), raw_packet):
                if c % 32 == 0:
                    text_packet += '\n{:06x} '.format(int(c / 2))
                elif c % 2 == 0:
                    text_packet += ' '
                text_packet += b
            text_packets.append(text_packet)

        if cap_type == XenaCaptureBufferType.text:
            self._save_captue(file_name, text_packets)
            return text_packets

        if cap_type is XenaCaptureBufferType.pcap and not tshark:
            from xenavalkyrie.xena_tshark import Tshark
            tshark = Tshark('')
        temp_file_name = file_name + '_'
        self._save_captue(temp_file_name, text_packets)
        tshark.text_to_pcap(temp_file_name, file_name)
        os.remove(temp_file_name)

    #
    # Properties.
    #

    @property
    def packets(self):
        """
        :return: dictionary {id: object} of all packets.
        :rtype: dict of (int, xenavalkyrie.xena_port.XenaCapturePacket)
        """

        if not self.get_object_by_type('cappacket'):
            for index in range(0, self.read_stats()['packets']):
                XenaCapturePacket(parent=self, index='{}/{}'.format(self.index, index))
        return {p.id: p for p in self.get_objects_by_type('cappacket')}

    #
    # Private methods.
    #

    def _save_captue(self, file_name, packets):
        if file_name:
            with open(file_name, 'w+') as f:
                for packet in packets:
                    f.write(packet)


class XenaCapturePacket(XenaObject21):
    """ Represents single captured packet. """

    _info_config_commands = ['pc_info']

    def __init__(self, parent, index):
        objRef = '{}/{}'.format(parent.ref, index.split('/')[-1])
        super(self.__class__, self).__init__(objType='cappacket', parent=parent, index=index, objRef=objRef)

class XenaFilter(XenaObject21):

    create_command = 'PF_CREATE'
    _info_config_commands = ['PF_CONFIG']
    stats_captions = {'PR_FILTER': ['bps', 'pps', 'bytes', 'packets']}

    def __init__(self, parent, index):
        objRef = '{}/{}'.format(parent.ref, index.split('/')[-1])
        super(self.__class__, self).__init__(objType='xena_filter', parent=parent, index=index, objRef=objRef)

    def read_stats(self):
        stats_with_captions = OrderedDict()
        for stat_name in self.stats_captions.keys():
            stats_with_captions[stat_name] = self.read_stat(self.stats_captions[stat_name], stat_name)
        return stats_with_captions

    def set_state(self, state):
        state = 'ON' if state else 'OFF'
        self.set_attributes(PF_ENABLE=state)

    def del_object_from_parent(self):
        super(self.__class__, self).del_object_from_parent()