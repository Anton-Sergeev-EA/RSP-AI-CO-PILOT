// RSP COPILOT — telemetry_engine
//
// Потоковая обработка виброметрии и температуры подшипников энергоблока.
// Два независимых механизма обнаружения:
//   1) Абсолютный порог — виброскорость превышает зону C (по аналогии с
//      логикой ISO 10816 для крупных вращающихся машин, упрощённо для демо) —
//      классический подход, применяемый в реальных системах защиты.
//   2) Статистический тренд-детектор — отклонение от пуско-наладочной
//      базовой линии, сглаженное EWMA, с устойчивым срабатыванием —
//      ловит развивающуюся проблему до того, как виброскорость дойдёт
//      до абсолютного порога.
//
// Протокол ввода (stdin): <day>,<vibration_mm_s>,<bearing_temp_c>\n на строку
// Протокол вывода (stdout): один JSON-объект с результатом анализа.
//
// Режим бенчмарка: telemetry_engine --bench N

#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include <deque>
#include <cmath>
#include <chrono>
#include <random>
#include <iomanip>

struct Sample {
    int day;
    double vib;
    double temp;
};

struct RollingStats {
    std::deque<double> window;
    double sum = 0.0, sumsq = 0.0;
    size_t cap;
    explicit RollingStats(size_t capacity) : cap(capacity) {}
    void push(double v) {
        window.push_back(v);
        sum += v; sumsq += v * v;
        if (window.size() > cap) {
            double old = window.front();
            window.pop_front();
            sum -= old; sumsq -= old * old;
        }
    }
    double mean() const { return window.empty() ? 0.0 : sum / static_cast<double>(window.size()); }
    double stddev() const {
        if (window.size() < 2) return 1e-6;
        double m = mean();
        double var = sumsq / static_cast<double>(window.size()) - m * m;
        return std::sqrt(std::max(var, 1e-6));
    }
};

double linreg_slope(const std::deque<double>& ys) {
    size_t n = ys.size();
    if (n < 3) return 0.0;
    double sx = 0, sy = 0, sxy = 0, sxx = 0;
    for (size_t i = 0; i < n; ++i) {
        double x = static_cast<double>(i), y = ys[i];
        sx += x; sy += y; sxy += x * y; sxx += x * x;
    }
    double denom = (static_cast<double>(n) * sxx - sx * sx);
    if (std::abs(denom) < 1e-9) return 0.0;
    return (static_cast<double>(n) * sxy - sx * sy) / denom;
}

// Упрощённые (иллюстративные) зоны виброскорости — см. комментарий в шапке файла.
constexpr double VIB_ZONE_B = 2.8;
constexpr double VIB_ZONE_C = 4.5;

struct AnalysisResult {
    std::string status = "OK";
    std::string trigger = "none";        // "absolute_threshold" | "trend" | "none"
    int trend_anomaly_day = -1;
    int absolute_threshold_day = -1;
    double rul_days_estimate = -1.0;
    std::vector<double> smoothed;
};

AnalysisResult analyze(const std::vector<Sample>& samples, size_t baseline_window = 20,
                        double ewma_alpha = 0.1, double anomaly_threshold = 3.0,
                        int sustained_days_required = 10, size_t trend_window = 14) {
    AnalysisResult res;

    RollingStats baseline(baseline_window);
    size_t n_for_baseline = std::min(baseline_window, samples.size());
    for (size_t i = 0; i < n_for_baseline; ++i) baseline.push(samples[i].vib);
    double baseline_mean = baseline.mean();
    double baseline_std = baseline.stddev();

    double ewma = 0.0;
    bool ewma_init = false;
    int consecutive = 0;
    int first_trend_day = -1;
    int first_abs_day = -1;

    for (const auto& s : samples) {
        if (first_abs_day == -1 && s.vib >= VIB_ZONE_C) first_abs_day = s.day;

        double z = (s.vib - baseline_mean) / baseline_std;
        if (!ewma_init) { ewma = z; ewma_init = true; }
        else { ewma = ewma_alpha * z + (1 - ewma_alpha) * ewma; }
        res.smoothed.push_back(ewma);

        if (ewma > anomaly_threshold) {
            consecutive++;
            if (consecutive >= sustained_days_required && first_trend_day == -1) {
                first_trend_day = s.day - sustained_days_required + 1;
            }
        } else {
            consecutive = 0;
        }
    }

    res.trend_anomaly_day = first_trend_day;
    res.absolute_threshold_day = first_abs_day;

    if (first_abs_day >= 0) {
        res.status = "CRITICAL";
        res.trigger = "absolute_threshold";
    } else if (first_trend_day >= 0) {
        res.status = "CRITICAL";
        res.trigger = "trend";
    } else if (!res.smoothed.empty() && res.smoothed.back() > anomaly_threshold * 0.6) {
        res.status = "WARNING";
        res.trigger = "trend";
    }

    if (samples.size() > trend_window * 2) {
        double critical_level = VIB_ZONE_C;
        std::deque<double> recent;
        size_t start = samples.size() > trend_window ? samples.size() - trend_window : 0;
        for (size_t i = start; i < samples.size(); ++i) recent.push_back(samples[i].vib);
        double slope = linreg_slope(recent);
        double current_val = samples.back().vib;
        if (slope > 0.0005 && current_val < critical_level) {
            res.rul_days_estimate = (critical_level - current_val) / slope;
        } else if (current_val >= critical_level) {
            res.rul_days_estimate = 0.0;
        } else {
            res.rul_days_estimate = -1.0;
        }
    }

    return res;
}

std::string to_json(const AnalysisResult& r) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(4);
    out << "{";
    out << "\"status\":\"" << r.status << "\",";
    out << "\"trigger\":\"" << r.trigger << "\",";
    out << "\"trend_anomaly_day\":" << r.trend_anomaly_day << ",";
    out << "\"absolute_threshold_day\":" << r.absolute_threshold_day << ",";
    out << "\"rul_days_estimate\":" << r.rul_days_estimate << ",";
    out << "\"smoothed_zscore\":[";
    for (size_t i = 0; i < r.smoothed.size(); ++i) {
        out << r.smoothed[i];
        if (i + 1 < r.smoothed.size()) out << ",";
    }
    out << "]";
    out << "}";
    return out.str();
}

void run_stream_mode() {
    std::vector<Sample> samples;
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        std::stringstream ss(line);
        std::string tok;
        Sample s{};
        std::getline(ss, tok, ','); s.day = std::stoi(tok);
        std::getline(ss, tok, ','); s.vib = std::stod(tok);
        std::getline(ss, tok, ','); s.temp = std::stod(tok);
        samples.push_back(s);
    }
    auto res = analyze(samples);
    std::cout << to_json(res) << std::endl;
}

void run_benchmark(long n) {
    std::mt19937 gen(42);
    std::normal_distribution<double> noise(0.0, 0.12);
    std::vector<Sample> samples;
    samples.reserve(n);
    double v = 1.8;
    for (long i = 0; i < n; ++i) {
        v += noise(gen) * 0.01;
        samples.push_back({static_cast<int>(i), v, 52.0 + noise(gen) * 2});
    }
    auto t0 = std::chrono::high_resolution_clock::now();
    auto res = analyze(samples);
    auto t1 = std::chrono::high_resolution_clock::now();
    double seconds = std::chrono::duration<double>(t1 - t0).count();
    double throughput = static_cast<double>(n) / std::max(seconds, 1e-9);

    std::ostringstream out;
    out << std::fixed << std::setprecision(2);
    out << "{\"benchmark_points\":" << n << ",";
    out << "\"elapsed_seconds\":" << seconds << ",";
    out << "\"throughput_points_per_sec\":" << throughput << ",";
    out << "\"status\":\"" << res.status << "\"}";
    std::cout << out.str() << std::endl;
}

int main(int argc, char** argv) {
    if (argc >= 3 && std::string(argv[1]) == "--bench") {
        run_benchmark(std::stol(argv[2]));
        return 0;
    }
    run_stream_mode();
    return 0;
}
