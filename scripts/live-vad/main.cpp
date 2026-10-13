// Persistent, bounded PCM16 -> Silero speech probabilities worker.
#include "whisper.h"
#include <cstdint>
#include <cstdio>
#include <vector>
#include <cstring>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif
int main(int argc, char **argv) {
    if (argc == 2 && !std::strcmp(argv[1], "--help")) {
        std::puts("lecorder-vad MODEL: uint32 LE sample count + PCM16 LE; JSON probabilities on stdout");
        return 0;
    }
    if (argc != 2) return 2;
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
#endif
    auto params = whisper_vad_default_context_params();
    params.use_gpu = false;
    params.n_threads = 2;
    auto *ctx = whisper_vad_init_from_file_with_params(argv[1], params);
    if (!ctx) return 3;
    unsigned char header[4];
    while (std::fread(header, 1, 4, stdin) == 4) {
        uint32_t n = uint32_t(header[0]) | uint32_t(header[1]) << 8 | uint32_t(header[2]) << 16 | uint32_t(header[3]) << 24;
        if (!n || n > 21 * 16000) break;
        std::vector<unsigned char> pcm(n * 2);
        if (std::fread(pcm.data(), 2, n, stdin) != n) break;
        std::vector<float> audio(n);
        for (uint32_t i = 0; i < n; ++i) audio[i] = int16_t(uint16_t(pcm[2*i]) | uint16_t(pcm[2*i+1]) << 8) / 32768.f;
        if (!whisper_vad_detect_speech(ctx, audio.data(), int(n))) break;
        const int count = whisper_vad_n_probs(ctx);
        const float *p = whisper_vad_probs(ctx);
        std::printf("[");
        for (int i = 0; i < count; ++i) std::printf("%s%.5f", i ? "," : "", p[i]);
        std::printf("]\n");
        std::fflush(stdout);
    }
    whisper_vad_free(ctx);
    return 0;
}
