"""PyAV audio operations, isolated so native decoding can be cancelled."""
import json
import sys


def transcode(source, destination, playback=False):
    import av
    codec = "pcm_s16le"
    rate = 16000
    container_format = "wav"
    options = {}
    if playback:
        rate = 48000
        if destination.lower().endswith(".mp4"):
            codec, container_format = "aac", "mp4"
            options = {"movflags": "+faststart"}
        else:
            codec, container_format = "libopus", "webm"
    with av.open(source) as incoming:
        if not incoming.streams.audio:
            raise ValueError("음성 트랙이 없는 파일입니다.")
        audio = incoming.streams.audio[0]
        with av.open(destination, "w", format=container_format, options=options) as output:
            stream = output.add_stream(codec, rate=rate)
            channels = audio.codec_context.channels
            stream.layout = ({1: "mono", 2: "stereo"}.get(channels, audio.codec_context.layout.name)
                             if playback else "mono")
            if playback:
                stream.bit_rate = 128000
                if codec == "libopus":
                    stream.options = {"frame_duration": "20", "application": "audio", "vbr": "on", "compression_level": "10"}
            graph = av.filter.Graph()
            inlet = graph.add_abuffer(template=audio)
            resample = graph.add("aresample", f"{rate}:async=1:first_pts=0" if playback else str(rate))
            formats = graph.add("aformat", f"sample_fmts={stream.codec_context.format.name}:channel_layouts={stream.layout.name}")
            sink = graph.add("abuffersink")
            inlet.link_to(resample); resample.link_to(formats); formats.link_to(sink)
            graph.configure()
            def drain():
                while True:
                    try:
                        frame = sink.pull()
                    except (av.error.BlockingIOError, av.error.EOFError):
                        break
                    for packet in stream.encode(frame):
                        output.mux(packet)
            end_time = ((audio.start_time or 0) + audio.duration) * audio.time_base if audio.duration else None
            for frame in incoming.decode(audio):
                # AAC may decode encoder padding past the MP4 edit-list duration.
                # Trim before resampling, as the command-line decoder does.
                if end_time is not None and frame.pts is not None:
                    remaining = max(0, round((end_time - frame.pts * frame.time_base) * frame.sample_rate))
                    if not remaining:
                        continue
                    if remaining < frame.samples:
                        fifo = av.AudioFifo()
                        pts = frame.pts
                        frame.pts = None
                        fifo.write(frame)
                        frame = fifo.read(remaining)
                        frame.pts = pts
                inlet.push(frame); drain()
            inlet.push(None); drain()
            for packet in stream.encode(None):
                output.mux(packet)
    return {}


def probe(source):
    import av
    with av.open(source) as container:
        duration = float(container.duration / av.time_base) if container.duration else 0.0
        lengths = []
        if container.streams.audio:
            for packet in container.demux(container.streams.audio[0]):
                if packet.duration and packet.time_base:
                    lengths.append(float(packet.duration * packet.time_base))
                if sum(lengths) >= 1 or len(lengths) >= 1000:
                    break
        return {"duration": duration,
                "short_frames": len(lengths) >= 4 and sum(n < .01 for n in lengths) > len(lengths)/2}


def clip(source, destination, start, duration):
    import wave
    with wave.open(source, "rb") as audio:
        rate = audio.getframerate()
        audio.setpos(min(audio.getnframes(), round(start * rate)))
        with wave.open(destination, "wb") as output:
            output.setparams(audio.getparams())
            remaining = round(duration * rate)
            while remaining > 0:
                count = min(remaining, rate)
                output.writeframes(audio.readframes(count))
                remaining -= count
    return {}


def silences(source):
    import wave
    from array import array
    # Matches silencedetect -35 dB / 250 ms for our mono signed-16 WAV.
    threshold = 32768 * 10 ** (-35 / 20)
    quiet, position, points = None, 0, []
    with wave.open(source, "rb") as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise ValueError("무음 탐지는 16-bit mono WAV가 필요합니다.")
        rate = audio.getframerate()
        while block := audio.readframes(rate):
            samples = array("h", block)
            if sys.byteorder != "little": samples.byteswap()
            for value in samples:
                if abs(value) <= threshold:
                    if quiet is None: quiet = position
                elif quiet is not None:
                    if position - quiet >= rate * .25:
                        points.append((quiet + position) / (2 * rate))
                    quiet = None
                position += 1
        if quiet is not None and position - quiet >= rate * .25:
            points.append((quiet + position) / (2 * rate))
    return {"points": points}


if __name__ == "__main__":
    request = json.load(sys.stdin)
    operation = request.pop("operation")
    functions = {"transcode": transcode, "probe": probe, "clip": clip, "silences": silences}
    print(json.dumps(functions[operation](**request)))
