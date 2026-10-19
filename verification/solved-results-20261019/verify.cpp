// Check the existing published solutions with the worker's native arithmetic.
#include "ec_core.h"
#include <iomanip>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>

ecc::UInt384 parse_hex(const std::string& text) {
    if (text.size() < 3 || text.substr(0, 2) != "0x" || text.size() > 98)
        throw std::runtime_error("invalid hex input");
    ecc::UInt384 value{};
    for (std::size_t i = 2; i < text.size(); ++i) {
        const char c = text[i];
        const int digit = c >= '0' && c <= '9' ? c - '0'
                        : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
        if (digit < 0) throw std::runtime_error("invalid hex digit");
        value = ecc::shift_left(value, 4);
        value.w[0] |= static_cast<unsigned>(digit);
    }
    return value;
}

std::string to_hex(const ecc::UInt384& value) {
    std::ostringstream out;
    out << "0x" << std::hex;
    int i = ecc::WORDS - 1;
    while (i > 0 && value.w[i] == 0) --i;
    out << value.w[i--];
    while (i >= 0) out << std::setw(8) << std::setfill('0') << value.w[i--];
    return out.str();
}

ecc::Point multiply(const ecc::UInt384& scalar, ecc::Point base, const ecc::Job& job) {
    ecc::Point result = ecc::identity(), next{};
    const int bits = ecc::bit_length(scalar);
    for (int i = 0; i < bits; ++i) {
        if (ecc::bit(scalar, i)) {
            if (!ecc::point_add(result, base, job, next))
                throw std::runtime_error("scalar addition failed");
            result = next;
        }
        if (i + 1 < bits) {
            if (!ecc::point_add(base, base, job, next))
                throw std::runtime_error("scalar doubling failed");
            base = next;
        }
    }
    return result;
}

int main() {
    try {
        std::set<std::string> seen;
        std::string line;
        while (std::getline(std::cin, line)) {
            std::istringstream input(line);
            std::string id, modulus, a, b, n, gx, gy, qx, qy, scalar, extra;
            ecc::Job job{};
            if (!(input >> id >> job.kind >> job.field_bits >> modulus >> a >> b >> n
                        >> gx >> gy >> qx >> qy >> scalar) || input >> extra)
                throw std::runtime_error("invalid registry row");
            if (!seen.insert(id).second || job.kind > 1 || job.field_bits > 359)
                throw std::runtime_error("invalid or duplicate challenge");
            job.modulus = parse_hex(modulus); job.a = parse_hex(a); job.b = parse_hex(b);
            job.n = parse_hex(n);
            const ecc::Point p{parse_hex(gx), parse_hex(gy), 0};
            const ecc::Point q{parse_hex(qx), parse_hex(qy), 0};
            const ecc::UInt384 k = parse_hex(scalar);
            if (ecc::is_zero(k) || ecc::compare(k, job.n) >= 0
                || !ecc::on_curve(p, job) || !ecc::on_curve(q, job))
                throw std::runtime_error(id + ": invalid point or scalar");
            const ecc::Point computed = multiply(k, p, job);
            const ecc::Point wrong = multiply(ecc::add_raw(k, ecc::small(1)), p, job);
            if (!ecc::point_equal(computed, q) || ecc::point_equal(wrong, q))
                throw std::runtime_error(id + ": solution or negative control failed");
            std::cout << "{\"challenge\":\"" << id << "\",\"scalar\":\"" << scalar
                      << "\",\"computed_q\":[\"" << to_hex(computed.x) << "\",\""
                      << to_hex(computed.y) << "\"],\"kP_equals_Q\":true,"
                         "\"k_plus_one_rejected\":true}\n";
        }
        if (seen.size() != 10) throw std::runtime_error("expected all ten published solutions");
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
