#include <kiwi/capi.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <stdexcept>
using nlohmann::json;
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    kiwi_h kiwi = nullptr;
    kiwi_res_h result = nullptr;
    try {
        json input; std::cin >> input;
        if (!input.is_array()) throw std::runtime_error("Expected text array");
        kiwi = kiwi_init(argv[1], 2, KIWI_BUILD_DEFAULT, 0);
        if (!kiwi) throw std::runtime_error(kiwi_error());
        json output = json::array();
        for (const auto& text : input) {
            kiwi_analyze_option_t options{};
            result = kiwi_analyze(kiwi, text.get<std::string>().c_str(), 1, options, nullptr);
            if (!result) throw std::runtime_error(kiwi_error());
            json tokens = json::array();
            for (int i = 0; i < kiwi_res_word_num(result, 0); ++i) {
                const auto* info = kiwi_res_token_info(result, 0, i);
                tokens.push_back({{"form", kiwi_res_form(result, 0, i)},
                    {"tag", kiwi_res_tag(result, 0, i)}, {"start", info->chr_position},
                    {"length", info->length}, {"word", info->word_position}, {"line", info->line_number}});
            }
            output.push_back(tokens);
            kiwi_res_close(result); result = nullptr;
        }
        kiwi_close(kiwi); kiwi = nullptr;
        std::cout << output.dump() << std::endl;
        return 0;
    } catch (const std::exception& e) {
        if (result) kiwi_res_close(result);
        if (kiwi) kiwi_close(kiwi);
        std::cerr << e.what() << std::endl;
        return 1;
    }
}
