# M25 -- the USB side of the autonomous test loop.
#
# The FFT suite needs two things from the board over one cable: MIDI down and audio up. The SDK
# gives us exactly half of that. `tiliqua.usb_audio.USB2AudioInterface` is a working USB Audio
# Class 2 device, so audio up is solved. MIDI down is not: the SDK's only USB-MIDI is
# `USBMIDIHost` from the `guh` package (src/top/usb_host/top.py), which makes Tiliqua the *host*
# for a keyboard plugged into it -- the opposite direction, and it cannot share the `usb2` port
# with a device-mode stack anyway. luna itself ships no MIDI class at all.
#
# So this file adds a MIDIStreaming *function* to the SDK's audio device. It does not vendor the
# SDK's 555 lines; it subclasses, using two properties of the parent that were checked rather than
# assumed:
#
#   - `create_descriptors()` is self-contained and called from `elaborate()`, so an override is
#     picked up. It cannot be *extended* -- `DeviceDescriptorCollection.ConfigurationDescriptor()`
#     is a context manager that seals the configuration on exit -- so the method body is restated
#     here with the MIDI function inserted inside the `with` block. The three heavy helpers
#     (`create_audio_control_interface_descriptor`, `create_{output,input}_channels_descriptor`)
#     are still the parent's, untouched.
#   - `USBDevice.add_endpoint()` (luna/gateware/usb/usb2/device.py:161) only appends to a list.
#     Amaranth resolves `m.submodules.usb` on read, so `elaborate()` can call `super().elaborate()`
#     and add the bulk endpoint to the returned module before the fragment is ever built.
#   - That same list is how M32 reaches the parent's capture endpoint to re-drive its packet size.
#     Within one module and one domain a later `m.d.comb +=` wins outright, so the override does
#     not have to be threaded through the parent -- see `pace_capture_endpoint` below.
#
# EP 1 OUT / 1 IN / 2 IN and interfaces 0/1/2 belong to the audio function; the MIDI function
# takes EP 3 OUT and interfaces 3/4, behind its own Interface Association Descriptor.
# `UAC2RequestHandlers` already claims SET_INTERFACE for *every* interface number and always ACKs
# (usb_audio/__init__.py:482) -- its `m.Switch` only chooses which alt-setting register to update
# -- so the new interfaces need no request-handler work.

from amaranth import *
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out

from luna.usb2 import USBIsochronousStreamInEndpoint, USBStreamOutEndpoint
from usb_protocol.emitters import DeviceDescriptorCollection
from usb_protocol.emitters.descriptors import midi1, standard, uac2
from usb_protocol.types import USBDirection
from usb_protocol.types.descriptors.midi1 import MidiStreamingJackTypes

from tiliqua.midi.decode_usb import USBMidiCIN
from tiliqua.usb_audio import USB2AudioInterface


class UsbMidiUnpack(wiring.Component):

    """
    Unpack 4-byte USB-MIDI event packets into the raw MIDI byte stream the engine expects.

    Each packet is a header byte (cable number in the high nibble, code index number in the low
    nibble) followed by three bytes of which only the first `n` are real, where `n` is implied by
    the CIN. Everything else on the wire -- running status, SysEx framing, System Common -- has
    already been normalised into packets by the host's USB-MIDI driver, so this only has to know
    the length table and drop the padding.

    No filtering happens here. Bytes go on to the same MidiRTFilter / MidiSysexFilter /
    SysCommonFilter chain M24 built for the TRS jack, which is where the engine's
    every-byte-over-0x80-is-a-status hazard (core/synth.x:114) is dealt with.

    Domain-agnostic: instantiated under DomainRenamer("usb") below.
    """

    i: In(stream.Signature(unsigned(8)))
    o: Out(stream.Signature(unsigned(8)))

    def elaborate(self, platform):
        m = Module()

        # Byte position within the current event packet: 0 is the header, 1..3 the payload.
        idx = Signal(2)
        # Payload bytes this packet actually carries, latched when its header went past.
        n = Signal(2)

        cin = Signal(USBMidiCIN)
        m.d.comb += cin.eq(self.i.payload[0:4])

        n_hdr = Signal(2)
        with m.Switch(cin):
            # Reserved for future expansion; nothing to hand on.
            with m.Case(USBMidiCIN.MISC, USBMidiCIN.CABLE_EVENT):
                m.d.comb += n_hdr.eq(0)
            with m.Case(USBMidiCIN.SYSEX_END_1, USBMidiCIN.SINGLE_BYTE):
                m.d.comb += n_hdr.eq(1)
            with m.Case(USBMidiCIN.SYSTEM_COMMON_2, USBMidiCIN.SYSEX_END_2,
                        USBMidiCIN.PROGRAM_CHANGE, USBMidiCIN.CHANNEL_PRESSURE):
                m.d.comb += n_hdr.eq(2)
            with m.Default():
                m.d.comb += n_hdr.eq(3)

        header = idx == 0
        forward = ~header & (idx <= n)

        m.d.comb += [
            self.o.payload.eq(self.i.payload),
            self.o.valid.eq(self.i.valid & forward),
            # Header and padding bytes are swallowed unconditionally, so a stalled consumer can
            # never wedge the packet counter halfway through a packet.
            self.i.ready.eq(self.o.ready | ~forward),
        ]

        with m.If(self.i.valid & self.i.ready):
            m.d.sync += idx.eq(idx + 1)
            with m.If(header):
                m.d.sync += n.eq(n_hdr)

        return m


class XlsUsbInterface(USB2AudioInterface):

    """
    The SDK's UAC2 device plus a MIDIStreaming function on EP 3 OUT.

    `o_midi` carries the received MIDI bytes, in the `usb` domain. It is a plain created interface
    rather than a signature member because `USB2AudioInterface.__init__` passes a fixed dict to
    `wiring.Component.__init__`, leaving a subclass no way to add ports to the signature.
    """

    # 0/1/2 belong to the audio function.
    AC_INTERFACE = 3
    MS_INTERFACE = 4
    # Jack IDs are function-local. Data enters the function through the embedded MIDI IN jack and
    # leaves it, notionally, through an external MIDI OUT jack -- which here is the synth engine.
    IN_JACK = 1
    OUT_JACK = 2
    MIDI_ENDPOINT = 3
    # USB 2.0 s5.8.3: a high-speed bulk endpoint must declare 512 bytes, and this device
    # enumerates at high speed (`usb.full_speed_only` is tied low upstream). MIDI arrives a few
    # bytes at a time and is drained immediately, so the buffer is one packet rather than luna's
    # default two -- the endpoint NAKs instead of double-buffering, which costs nothing here.
    MIDI_MAX_PACKET = 512

    def __init__(self, *, audio_clock, nr_channels):
        super().__init__(audio_clock=audio_clock, nr_channels=nr_channels)
        self.o_midi = stream.Signature(unsigned(8)).create()

    def create_descriptors(self):
        """ The parent's descriptors, with a second function for MIDI. """

        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.bcdUSB = 2.00
            # Miscellaneous / Common Class / Interface Association Descriptor: required for a
            # device whose interfaces are grouped into functions by IADs, which now there are two
            # of.
            d.bDeviceClass = 0xEF
            d.bDeviceSubclass = 0x02
            d.bDeviceProtocol = 0x01
            # apf.audio's VID/PID, unchanged -- this is still a Tiliqua, and squatting a different
            # pid.codes allocation would be worse than sharing. iProduct is what the host
            # transport matches on, so that is what distinguishes this bitstream.
            d.idVendor = 0x1209
            d.idProduct = 0xAA62

            d.iManufacturer = "apf.audio"
            d.iProduct = "Tiliqua XLS32"
            d.iSerialNumber = "beta-0000"
            d.bcdDevice = 0.01

            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as configDescr:
            # --- audio function: interfaces 0, 1, 2 ---------------------------------------
            interfaceAssociationDescriptor = uac2.InterfaceAssociationDescriptorEmitter()
            interfaceAssociationDescriptor.bInterfaceCount = 3
            configDescr.add_subordinate_descriptor(interfaceAssociationDescriptor)

            interfaceDescriptor = uac2.StandardAudioControlInterfaceDescriptorEmitter()
            interfaceDescriptor.bInterfaceNumber = 0
            configDescr.add_subordinate_descriptor(interfaceDescriptor)

            configDescr.add_subordinate_descriptor(
                self.create_audio_control_interface_descriptor())

            self.create_output_channels_descriptor(configDescr)
            self.create_input_channels_descriptor(configDescr)

            # --- MIDI function: interfaces 3, 4 -------------------------------------------
            self.create_midi_function_descriptor(configDescr)

        return descriptors

    def create_midi_function_descriptor(self, c):
        """
        A USB MIDI 1.0 function: its own IAD, an AudioControl interface, a MIDIStreaming
        interface with one embedded IN jack, and a bulk OUT endpoint.

        MIDI 1.0 s3.1 requires the MIDIStreaming interface to sit behind an AudioControl
        interface, and that AudioControl interface is UAC *1* shaped even on a USB 2.0 device --
        protocol 0x00, and a class-specific header that enumerates its streaming interfaces.
        UAC2's emitters cannot express it (`bInterfaceProtocol` there is a fixed
        `IP_VERSION_02_00`) and usb_protocol has no UAC1 header emitter, so the header is nine
        literal bytes below.
        """

        iad = standard.InterfaceAssociationDescriptorEmitter()
        iad.bFirstInterface = self.AC_INTERFACE
        iad.bInterfaceCount = 2
        iad.bFunctionClass = 0x01     # AUDIO
        iad.bFunctionSubClass = 0x01  # AUDIO_CONTROL
        iad.bFunctionProtocol = 0x00  # UAC1
        c.add_subordinate_descriptor(iad)

        acInterface = standard.InterfaceDescriptorEmitter()
        acInterface.bInterfaceNumber = self.AC_INTERFACE
        acInterface.bAlternateSetting = 0
        acInterface.bNumEndpoints = 0
        acInterface.bInterfaceClass = 0x01     # AUDIO
        acInterface.bInterfaceSubclass = 0x01  # AUDIO_CONTROL
        acInterface.bInterfaceProtocol = 0x00  # UAC1
        c.add_subordinate_descriptor(acInterface)

        # UAC1 Table 4-2, class-specific AudioControl interface header:
        #   bLength 9, bDescriptorType CS_INTERFACE (0x24), bDescriptorSubtype HEADER (0x01),
        #   bcdADC 1.00, wTotalLength 9 (this header and nothing else -- there are no units or
        #   terminals in the MIDI function), bInCollection 1, baInterfaceNr(1) = the MS interface.
        c.add_subordinate_descriptor(bytes([
            0x09, 0x24, 0x01, 0x00, 0x01, 0x09, 0x00, 0x01, self.MS_INTERFACE,
        ]))

        msInterface = midi1.StandardMidiStreamingInterfaceDescriptorEmitter()
        msInterface.bInterfaceNumber = self.MS_INTERFACE
        msInterface.bAlternateSetting = 0
        msInterface.bNumEndpoints = 1
        c.add_subordinate_descriptor(msInterface)

        # The class-specific MS header's wTotalLength covers itself plus every jack descriptor,
        # which the emitter works out from its subordinates.
        msHeader = midi1.ClassSpecificMidiStreamingInterfaceDescriptorEmitter()

        inJack = midi1.MidiInJackDescriptorEmitter()
        inJack.bJackType = MidiStreamingJackTypes.EMBEDDED
        inJack.bJackID = self.IN_JACK
        msHeader.add_subordinate_descriptor(inJack)

        outJack = midi1.MidiOutJackDescriptorEmitter()
        outJack.bJackType = MidiStreamingJackTypes.EXTERNAL
        outJack.bJackID = self.OUT_JACK
        outJack.add_source(self.IN_JACK)
        msHeader.add_subordinate_descriptor(outJack)

        c.add_subordinate_descriptor(msHeader)

        midiOutEndpoint = midi1.StandardMidiStreamingBulkDataEndpointDescriptorEmitter()
        midiOutEndpoint.bEndpointAddress = \
            USBDirection.OUT.to_endpoint_address(self.MIDI_ENDPOINT)
        midiOutEndpoint.wMaxPacketSize = self.MIDI_MAX_PACKET
        c.add_subordinate_descriptor(midiOutEndpoint)

        midiOutEndpointClass = \
            midi1.ClassSpecificMidiStreamingBulkDataEndpointDescriptorEmitter()
        midiOutEndpointClass.add_associated_jack(self.IN_JACK)
        c.add_subordinate_descriptor(midiOutEndpointClass)

    # EP 2 IN is the parent's capture endpoint. Its packet size is one frame of every channel,
    # and USB Audio sends 32-bit samples even where the descriptor says 24
    # (usb_audio/channels_to_usb_stream.py:102), so a frame is four bytes per channel.
    CAPTURE_ENDPOINT = 2
    BYTES_PER_SAMPLE = 4
    # How far the buffer may sit from its target before the packet size moves. See
    # `pace_capture_endpoint` for why this is a dead zone and not a Schmitt trigger.
    PACE_BAND = 2

    def capture_endpoint(self, m):
        """ The parent's EP 2 IN, fished back out of the device it was added to. """

        for ep in m.submodules.usb._endpoints:
            if (isinstance(ep, USBIsochronousStreamInEndpoint)
                    and ep._endpoint_number == self.CAPTURE_ENDPOINT):
                return ep
        raise RuntimeError(
            "EP 2 IN is not where USB2AudioInterface.elaborate() left it. The rate control in "
            "pace_capture_endpoint() would silently do nothing, and USB captures would go back "
            "to dropping a run of frames every ten seconds -- fix this lookup, do not delete it.")

    def pace_capture_endpoint(self, m):
        """
        Drive the capture packet size from the device's own buffer level.

        The SDK computes that size from a counter only the *playback* stream updates
        (usb_audio/__init__.py:316-329), so with nothing playing it stays at its reset value of
        `24 * nr_channels`. At 48 kHz / 4 channels that is 96 bytes = 6 frames per microframe =
        exactly 48,000 frames/s -- the host's nominal rate, which is not the rate this board
        runs at. Measured over two takes the audio clock is 110-123 ppm fast, so the codec makes
        about 5.5 frames/s more than the host collects. The 48 frames of elasticity between them
        (`usb_tee` 16 + `adc_fifo` 16 + `out_fifo` 16) soak that up for roughly ten seconds and
        then the tee drops a run of ~60 frames: 0.011 % of a capture, a step in every sustained
        tone, once per 10.4 s. Enlarging any of those FIFOs buys more seconds and fixes nothing,
        because the rates still differ.

        A UAC2 asynchronous IN endpoint states its rate by varying how many samples it sends;
        the absence of a feedback endpoint on capture is the design, not an omission. So: one
        frame under nominal while the buffer is draining, one over while it is filling, nominal
        in between. The average lands on whatever the board actually produces. There is no ppm
        constant here and nothing to recalibrate if the crystal is ever replaced.

        Two things make the crude version of this safe.

        Aim at the middle. `bytes_in_frame` is latched once per microframe at SOF
        (luna .../endpoints/isochronous_stream_in.py:102), and over a microframe the level
        sawtooths by one packet's worth of frames as the host drains and the codec refills.
        Where in that sawtooth SOF falls depends on where the host schedules its IN token, which
        is the host's business and not observable from in here. Targeting the centre of the FIFO
        makes the answer irrelevant: a full swing either side of the midpoint still fits.

        Bias to overrun. The endpoint may only ask for the extra frame once the level is above
        `target + PACE_BAND`, which cannot happen unless `out_fifo` downstream is already full,
        so the extra frame is always in hand. That matters because underrunning is the worse
        failure: `ChannelsToUSBStream`'s FILL state pads short frames with zeroes, and a
        zero-padded frame is exactly what `rec_audio.py` counts as a dropout -- the fix would
        report itself as the bug.

        The cost is latency. Holding the midpoint keeps ~24 frames buffered, so the USB copy
        lags the jacks by about half a millisecond. The jacks are what anyone plays through.
        """

        nominal = self.fs // 8000                       # frames per 125 us microframe
        frame_bytes = self.BYTES_PER_SAMPLE * self.nr_channels
        # `AudioToChannels` is built with this depth in __init__ (usb_audio/__init__.py:420).
        target = (16 * (self.fs // 48000)) // 2
        assert (nominal + 1) * frame_bytes <= self.max_packet_size, \
            "one frame over nominal must still fit a single packet"

        # A dead zone rather than hysteresis: the two thresholds are distinct, so the decision
        # cannot chatter between the outer states, and chatter across one threshold costs a
        # single frame of correction that the next microframe undoes. A Schmitt trigger would
        # only earn its keep if one wrong decision were expensive, and here it is one frame.
        pace = Signal(range(0, 3073), init=nominal * frame_bytes)
        level = self.dbg.adc_fifo_level
        with m.If(level < target - self.PACE_BAND):
            m.d.usb += pace.eq((nominal - 1) * frame_bytes)
        with m.Elif(level > target + self.PACE_BAND):
            m.d.usb += pace.eq((nominal + 1) * frame_bytes)
        with m.Else():
            m.d.usb += pace.eq(nominal * frame_bytes)

        # Overrides the parent's `ep2_in.bytes_in_frame.eq(audio_in_frame_bytes)`: same module,
        # same domain, later statement. `pace` is registered, so nothing long-combinational
        # lands on a signal the endpoint samples at SOF.
        m.d.comb += self.capture_endpoint(m).bytes_in_frame.eq(pace)

    def elaborate(self, platform):
        m = super().elaborate(platform)

        self.pace_capture_endpoint(m)

        ep3_out = USBStreamOutEndpoint(
            endpoint_number=self.MIDI_ENDPOINT,
            max_packet_size=self.MIDI_MAX_PACKET,
            buffer_size=self.MIDI_MAX_PACKET)
        m.submodules.usb.add_endpoint(ep3_out)

        m.submodules.midi_unpack = unpack = DomainRenamer("usb")(UsbMidiUnpack())
        m.d.comb += [
            unpack.i.payload.eq(ep3_out.stream.payload),
            unpack.i.valid.eq(ep3_out.stream.valid),
            ep3_out.stream.ready.eq(unpack.i.ready),
        ]
        wiring.connect(m, unpack.o, wiring.flipped(self.o_midi))

        return m
