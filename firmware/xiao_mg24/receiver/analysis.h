#pragma once
#include "config.h"
#include <cmath>
#include <cstdint>

namespace mg24 {
struct Analysis {
    double mean = 0, rms = 0, peak40 = 0;
    uint16_t minimum = ADC_MAX, maximum = 0;
    bool rail = false;
};

// Least-squares fit of offset + a*cos(w*n) + b*sin(w*n). Actual fs is
// used, so a fractional number of carrier periods does not leak DC into a/b.
inline Analysis analyze(const uint16_t *samples, unsigned n, double sample_hz) {
    Analysis result;
    double gram[3][4] = {};
    for (unsigned i = 0; i < n; ++i) {
        const double angle = 2 * 3.14159265358979323846 * SIGNAL_HZ * i / sample_hz;
        const double x[3] = {1, std::cos(angle), std::sin(angle)};
        for (unsigned r = 0; r < 3; ++r) {
            for (unsigned c = 0; c < 3; ++c) gram[r][c] += x[r] * x[c];
            gram[r][3] += x[r] * samples[i];
        }
        result.mean += samples[i];
        if (samples[i] < result.minimum) result.minimum = samples[i];
        if (samples[i] > result.maximum) result.maximum = samples[i];
    }
    result.mean /= n;
    for (unsigned i = 0; i < n; ++i) {
        const double delta = samples[i] - result.mean;
        result.rms += delta * delta;
    }
    result.rms = std::sqrt(result.rms / n);
    result.rail = result.minimum == 0 || result.maximum >= ADC_MAX;
    for (unsigned col = 0; col < 3; ++col) {
        const double divisor = gram[col][col];
        if (std::abs(divisor) < 1e-8) return result;
        for (unsigned k = col; k < 4; ++k) gram[col][k] /= divisor;
        for (unsigned r = 0; r < 3; ++r) {
            if (r == col) continue;
            const double scale = gram[r][col];
            for (unsigned k = col; k < 4; ++k) gram[r][k] -= scale * gram[col][k];
        }
    }
    result.peak40 = std::hypot(gram[1][3], gram[2][3]);
    return result;
}
}
