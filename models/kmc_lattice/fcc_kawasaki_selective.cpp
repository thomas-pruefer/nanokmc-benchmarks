// Benchmark-owned KMC_Lattice application for the reduced homogeneous FCC Kawasaki model.
// Upstream KMC_Lattice source is used unmodified; this file only derives the application-specific
// Object/Event/Simulation classes required by the framework.

#include "Simulation.h"
#include "Event.h"
#include "Object.h"
#include "Site.h"
#include "Parameters_Simulation.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <list>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace KMC_Lattice;
namespace fs = std::filesystem;

namespace {

constexpr double COMMON_MCS_PER_NATIVE_TIME = 6.0;
constexpr std::array<std::array<int, 3>, 12> FCC_OFFSETS{{
    {{ 1,  1,  0}}, {{ 1, -1,  0}}, {{-1,  1,  0}}, {{-1, -1,  0}},
    {{ 1,  0,  1}}, {{ 1,  0, -1}}, {{-1,  0,  1}}, {{-1,  0, -1}},
    {{ 0,  1,  1}}, {{ 0,  1, -1}}, {{ 0, -1,  1}}, {{ 0, -1, -1}},
}};

struct Args {
    int fcc_cells = 0;
    double composition_a = 0.5;
    double kt = 1.0;
    double ea = 1.0;
    int seed = 1;
    std::vector<double> mcs_points;
    fs::path output_dir;
    int validation_events = 0;
    bool validate_rates_each_event = false;
    std::string recalc_mode = "selective";
};

std::vector<double> parse_csv_doubles(const std::string& text) {
    std::vector<double> values;
    std::stringstream ss(text);
    std::string token;
    while (std::getline(ss, token, ',')) {
        if (!token.empty()) values.push_back(std::stod(token));
    }
    return values;
}

Args parse_args(int argc, char** argv) {
    Args args;
    for (int i = 1; i < argc; ++i) {
        const std::string key = argv[i];
        auto need_value = [&](const std::string& name) -> std::string {
            if (i + 1 >= argc) throw std::invalid_argument("Missing value for " + name);
            return argv[++i];
        };
        if (key == "--fcc-cells") args.fcc_cells = std::stoi(need_value(key));
        else if (key == "--composition-a") args.composition_a = std::stod(need_value(key));
        else if (key == "--kt") args.kt = std::stod(need_value(key));
        else if (key == "--ea") args.ea = std::stod(need_value(key));
        else if (key == "--seed") args.seed = std::stoi(need_value(key));
        else if (key == "--mcs-points") args.mcs_points = parse_csv_doubles(need_value(key));
        else if (key == "--output-dir") args.output_dir = fs::path(need_value(key));
        else if (key == "--validation-events") args.validation_events = std::stoi(need_value(key));
        else if (key == "--validate-rates-each-event") args.validate_rates_each_event = true;
        else if (key == "--recalc-mode") args.recalc_mode = need_value(key);
        else if (key == "--help" || key == "-h") {
            std::cout
                << "Usage: kmc_lattice_fcc --fcc-cells L --composition-a X --kt X --ea X --seed S "
                << "--mcs-points 0,10,... --output-dir DIR [--recalc-mode selective] "
                << "[--validation-events N] [--validate-rates-each-event]\n";
            std::exit(0);
        }
        else throw std::invalid_argument("Unknown argument: " + key);
    }
    if (args.fcc_cells <= 0) throw std::invalid_argument("--fcc-cells must be positive");
    if (!(args.composition_a >= 0.0 && args.composition_a <= 1.0))
        throw std::invalid_argument("--composition-a must be in [0,1]");
    if (!(args.kt > 0.0)) throw std::invalid_argument("--kt must be positive");
    if (!(args.ea >= 0.0)) throw std::invalid_argument("--ea must be non-negative");
    if (args.output_dir.empty()) throw std::invalid_argument("--output-dir is required");
    if (args.recalc_mode != "selective")
        throw std::invalid_argument("--recalc-mode must be selective");
    if (args.mcs_points.empty()) args.mcs_points = {0.0};
    if (args.mcs_points.front() != 0.0)
        throw std::invalid_argument("--mcs-points must start at 0 for a fresh run");
    if (!std::is_sorted(args.mcs_points.begin(), args.mcs_points.end()))
        throw std::invalid_argument("--mcs-points must be sorted");
    // Event-by-event rate validation is an isolated regression check, not a
    // manuscript trajectory: its extra events are outside the evolution timer.
    if (args.validation_events < 0)
        throw std::invalid_argument("--validation-events must be non-negative");
    if (args.validate_rates_each_event && args.validation_events == 0)
        throw std::invalid_argument("--validate-rates-each-event requires positive --validation-events");
    if (args.validation_events > 0 && args.mcs_points != std::vector<double>{0.0})
        throw std::invalid_argument("--validation-events requires --mcs-points 0 (regression only)");
    return args;
}

std::string snapshot_name(double requested_mcs) {
    const long long label = static_cast<long long>(std::llround(requested_mcs));
    std::ostringstream ss;
    ss << "state_" << std::setw(8) << std::setfill('0') << label << ".bin";
    return ss.str();
}

class KawasakiMoveEvent;

class KawasakiObject : public Object {
public:
    KawasakiObject() : Object() {}
    KawasakiObject(double time, int tag, const Coords& coords) : Object(time, tag, coords) {}
    KawasakiMoveEvent* scheduled_event = nullptr;
    double outgoing_total_rate = 0.0;
};

class KawasakiMoveEvent : public Event {
public:
    KawasakiMoveEvent() : Event() {}
    explicit KawasakiMoveEvent(Simulation* sim) : Event(sim) {}
    std::string getEventType() const override { return "KawasakiMove"; }
};

struct ExecutedEventRecord {
    long long index = 0;
    double native_time = 0.0;
    Coords start{-1, -1, -1};
    Coords dest{-1, -1, -1};
    double selected_rate = 0.0;
    double object_total_rate = 0.0;
};

class FCCKawasakiSimulation : public Simulation {
public:
    void initialize(int fcc_cells, double composition_a, double kt, double ea, int seed) {
        fcc_cells_ = fcc_cells;
        period_ = 2 * fcc_cells_;
        kt_ = kt;
        ea_ = ea;

        Parameters_Simulation params;
        params.Enable_logging = false;
        params.Params_lattice.Enable_periodic_x = true;
        params.Params_lattice.Enable_periodic_y = true;
        params.Params_lattice.Enable_periodic_z = true;
        params.Params_lattice.Length = period_;
        params.Params_lattice.Width = period_;
        params.Params_lattice.Height = period_;
        params.Params_lattice.Unit_size = 1.0;
        params.Temperature = 1; // Reduced benchmark rate is supplied explicitly below.
        params.Enable_FRM = false;
        params.Enable_selective_recalc = true;
        // A changed site can affect a candidate A->B rate whose A source is at most two
        // FCC nearest-neighbour steps away. On the doubled FCC grid that is r^2 <= 8;
        // the framework accepts an integer cutoff, so 3 lattice units safely covers it.
        params.Recalc_cutoff = 3;
        params.Enable_full_recalc = false;
        Simulation::init(params, 0);

        Site site;
        sites_.assign(static_cast<size_t>(lattice.getNumSites()), site);
        std::vector<Site*> site_ptrs;
        site_ptrs.reserve(sites_.size());
        for (auto& s : sites_) site_ptrs.push_back(&s);
        if (!lattice.setSitePointers(site_ptrs))
            throw std::runtime_error("Failed to initialize KMC_Lattice site pointers");

        std::vector<Coords> physical_sites;
        physical_sites.reserve(static_cast<size_t>(4) * fcc_cells_ * fcc_cells_ * fcc_cells_);
        for (int x = 0; x < period_; ++x) {
            for (int y = 0; y < period_; ++y) {
                for (int z = 0; z < period_; ++z) {
                    if (((x + y + z) & 1) == 0) physical_sites.emplace_back(x, y, z);
                }
            }
        }
        total_physical_sites_ = static_cast<long long>(physical_sites.size());
        const long long expected = static_cast<long long>(4) * fcc_cells_ * fcc_cells_ * fcc_cells_;
        if (total_physical_sites_ != expected)
            throw std::runtime_error("Internal FCC parity-grid site-count mismatch");

        std::mt19937_64 init_rng(static_cast<std::uint64_t>(seed));
        std::shuffle(physical_sites.begin(), physical_sites.end(), init_rng);
        n_a_initial_ = static_cast<long long>(std::llround(composition_a * total_physical_sites_));

        for (long long i = 0; i < n_a_initial_; ++i) {
            objects_.emplace_back(0.0, static_cast<int>(i), physical_sites[static_cast<size_t>(i)]);
            KawasakiObject* obj = &objects_.back();
            addObject(obj);
            move_events_.emplace_back(this);
            KawasakiMoveEvent* ev = &move_events_.back();
            ev->setObjectPtr(obj);
            obj->scheduled_event = ev;
        }

        // Separate initial-state randomization from the KMC clock/pathway RNG.
        setGeneratorSeed(seed);
        for (auto& obj : objects_) calculateNextEvent(&obj);
        executed_moves_ = 0;
        setTime(0.0);
    }

    bool checkFinished() const override { return false; }

    bool executeNextEvent() override {
        return executeNextEventBefore(std::numeric_limits<double>::infinity(), nullptr);
    }

    long long advanceToNativeTime(double target_native_time) {
        if (target_native_time + 1e-15 < getTime())
            throw std::runtime_error("Target observation time is before the last executed event");
        const long long before = executed_moves_;
        while (executeNextEventBefore(target_native_time, nullptr)) {}
        return executed_moves_ - before;
    }

    bool executeOne(ExecutedEventRecord* record = nullptr) {
        return executeNextEventBefore(std::numeric_limits<double>::infinity(), record);
    }

    long long executedMoves() const { return executed_moves_; }
    long long nA() const { return static_cast<long long>(objects_.size()); }
    long long nTotal() const { return total_physical_sites_; }
    int period() const { return period_; }

    long long countActiveBonds() const {
        long long total = 0;
        for (const auto& obj : objects_) {
            const Coords src = obj.getCoords();
            for (const auto& off : FCC_OFFSETS) {
                Coords dest;
                lattice.calculateDestinationCoords(src, off[0], off[1], off[2], dest);
                if (!lattice.isOccupied(dest)) ++total;
            }
        }
        return total;
    }

    double totalGeneratorRate() const {
        double total = 0.0;
        for (const auto& obj : objects_) total += computeOutgoingRate(obj);
        return total;
    }

    bool validateAllStoredOutgoingRates(double rel_tol = 1e-12) const {
        for (const auto& obj : objects_) {
            const double expected = computeOutgoingRate(obj);
            const double actual = obj.outgoing_total_rate;
            const double scale = std::max({1.0, std::abs(expected), std::abs(actual)});
            if (std::abs(expected - actual) > rel_tol * scale) {
                std::cerr << "Stale outgoing rate for object " << obj.getTag()
                          << ": expected=" << std::setprecision(17) << expected
                          << " actual=" << actual << "\n";
                return false;
            }
        }
        return true;
    }

    void writeSnapshot(const fs::path& path) const {
        fs::create_directories(path.parent_path());
        std::ofstream out(path, std::ios::binary);
        if (!out) throw std::runtime_error("Cannot open snapshot for writing: " + path.string());
        const char magic[8] = {'K','M','C','L','A','T','0','1'};
        out.write(magic, 8);
        const std::uint32_t period_u32 = static_cast<std::uint32_t>(period_);
        const std::uint64_t n_u64 = static_cast<std::uint64_t>(total_physical_sites_);
        out.write(reinterpret_cast<const char*>(&period_u32), sizeof(period_u32));
        out.write(reinterpret_cast<const char*>(&n_u64), sizeof(n_u64));
        for (int x = 0; x < period_; ++x) {
            for (int y = 0; y < period_; ++y) {
                for (int z = 0; z < period_; ++z) {
                    if (((x + y + z) & 1) != 0) continue;
                    const Coords c{x, y, z};
                    const std::uint8_t species = lattice.isOccupied(c) ? 0u : 1u;
                    out.write(reinterpret_cast<const char*>(&species), 1);
                }
            }
        }
        if (!out) throw std::runtime_error("Snapshot write failed: " + path.string());
    }

private:
    std::list<Site> sites_;
    std::list<KawasakiObject> objects_;
    std::list<KawasakiMoveEvent> move_events_;
    int fcc_cells_ = 0;
    int period_ = 0;
    double kt_ = 1.0;
    double ea_ = 1.0;
    long long n_a_initial_ = 0;
    long long total_physical_sites_ = 0;
    long long executed_moves_ = 0;

    int countANeighbors(const Coords& center) const {
        int count = 0;
        for (const auto& off : FCC_OFFSETS) {
            Coords dest;
            lattice.calculateDestinationCoords(center, off[0], off[1], off[2], dest);
            if (lattice.isOccupied(dest)) ++count;
        }
        return count;
    }

    double transitionRate(const Coords& src, const Coords& dest) const {
        // Current state: src=A and dest=B. n_i counts A around src in the current state.
        // For n_f, src becomes B after the swap, so subtract the source A from the
        // current A-neighbour count around dest.
        const int ni = countANeighbors(src);
        const int nf = countANeighbors(dest) - 1;
        const int delta = ni - nf;
        if (delta <= 0) return 1.0;
        return std::exp(-ea_ * static_cast<double>(delta) / kt_);
    }

    double computeOutgoingRate(const KawasakiObject& obj) const {
        double total = 0.0;
        const Coords src = obj.getCoords();
        for (const auto& off : FCC_OFFSETS) {
            Coords dest;
            lattice.calculateDestinationCoords(src, off[0], off[1], off[2], dest);
            if (!lattice.isOccupied(dest)) total += transitionRate(src, dest);
        }
        return total;
    }

    void calculateNextEvent(KawasakiObject* obj) {
        KawasakiMoveEvent* scheduled = obj->scheduled_event;
        if (scheduled == nullptr) throw std::runtime_error("Object is missing persistent move event");

        std::vector<KawasakiMoveEvent> candidates;
        candidates.reserve(12);
        std::vector<Event*> candidate_ptrs;
        candidate_ptrs.reserve(12);
        const Coords src = obj->getCoords();
        double total_rate = 0.0;

        for (const auto& off : FCC_OFFSETS) {
            Coords dest;
            lattice.calculateDestinationCoords(src, off[0], off[1], off[2], dest);
            if (lattice.isOccupied(dest)) continue;
            const double rate = transitionRate(src, dest);
            candidates.emplace_back(this);
            KawasakiMoveEvent& candidate = candidates.back();
            candidate.setObjectPtr(obj);
            candidate.setDestCoords(dest);
            candidate.setRateConstant(rate);
            candidate_ptrs.push_back(&candidate);
            total_rate += rate;
        }

        obj->outgoing_total_rate = total_rate;
        if (candidate_ptrs.empty()) {
            setObjectEvent(obj, nullptr);
            return;
        }

        // Use KMC_Lattice's own BKL pathway-selection helper. It selects one destination
        // proportional to its rate and schedules that selected pathway with an exponential
        // waiting time based on the total outgoing rate for this object.
        Event* selected = determinePathway(candidate_ptrs);
        scheduled->setObjectPtr(obj);
        scheduled->setDestCoords(selected->getDestCoords());
        scheduled->setRateConstant(selected->getRateConstant());
        if (!scheduled->setExecutionTime(selected->getExecutionTime()))
            throw std::runtime_error("Failed to set scheduled event execution time");
        setObjectEvent(obj, scheduled);
    }

    bool executeNextEventBefore(double target_native_time, ExecutedEventRecord* record) {
        const auto event_it = chooseNextEvent();
        Event* event_ptr = *event_it;
        if (event_ptr == nullptr) return false;
        if (event_ptr->getExecutionTime() > target_native_time) return false;

        KawasakiObject* obj = static_cast<KawasakiObject*>(event_ptr->getObjectPtr());
        const Coords start = obj->getCoords();
        const Coords dest = event_ptr->getDestCoords();
        if (lattice.isOccupied(dest))
            throw std::runtime_error("Selected KMC_Lattice Kawasaki destination is occupied");

        if (record != nullptr) {
            record->index = executed_moves_ + 1;
            record->native_time = event_ptr->getExecutionTime();
            record->start = start;
            record->dest = dest;
            record->selected_rate = event_ptr->getRateConstant();
            record->object_total_rate = obj->outgoing_total_rate;
        }

        setTime(event_ptr->getExecutionTime());
        moveObject(obj, dest);
        ++executed_moves_;

        const std::vector<Object*> recalc = findRecalcObjects(start, dest);
        for (Object* base_obj : recalc) calculateNextEvent(static_cast<KawasakiObject*>(base_obj));

        return true;
    }
};

void write_progress_header(std::ofstream& out) {
    out << "save_index,requested_mcs,requested_native_time,native_last_event_time,executed_moves,"
           "events_since_previous,evolution_wall_seconds_segment,evolution_wall_seconds_cumulative,"
           "snapshot,N_A,N_total,active_bonds,total_rate\n";
}

void write_progress_row(
    std::ofstream& out,
    int save_index,
    double requested_mcs,
    double requested_native_time,
    double native_last_event_time,
    long long executed_moves,
    long long events_since_previous,
    double wall_segment,
    double wall_cumulative,
    const std::string& snapshot,
    long long n_a,
    long long n_total,
    long long active_bonds,
    double total_rate) {
    out << save_index << ','
        << std::setprecision(17) << requested_mcs << ','
        << requested_native_time << ','
        << native_last_event_time << ','
        << executed_moves << ','
        << events_since_previous << ','
        << wall_segment << ','
        << wall_cumulative << ','
        << snapshot << ','
        << n_a << ','
        << n_total << ','
        << active_bonds << ','
        << total_rate << '\n';
}

} // namespace

int main(int argc, char** argv) {
    try {
        const Args args = parse_args(argc, argv);
        fs::create_directories(args.output_dir / "snapshots");

        FCCKawasakiSimulation sim;
        sim.initialize(args.fcc_cells, args.composition_a, args.kt, args.ea, args.seed);

        std::ofstream progress(args.output_dir / "progress.csv");
        if (!progress) throw std::runtime_error("Cannot open progress.csv");
        write_progress_header(progress);

        double cumulative_wall = 0.0;
        const double initial_mcs = 0.0;
        const double initial_native_time = initial_mcs / COMMON_MCS_PER_NATIVE_TIME;
        const std::string snap0 = snapshot_name(initial_mcs);
        sim.writeSnapshot(args.output_dir / "snapshots" / snap0);
        write_progress_row(
            progress, 0, initial_mcs, initial_native_time, sim.getTime(), sim.executedMoves(), 0, 0.0, cumulative_wall,
            std::string("snapshots/") + snap0, sim.nA(), sim.nTotal(),
            sim.countActiveBonds(), sim.totalGeneratorRate());

        for (size_t idx = 1; idx < args.mcs_points.size(); ++idx) {
            const double requested_mcs = args.mcs_points[idx];
            const double target_native_time = requested_mcs / COMMON_MCS_PER_NATIVE_TIME;
            const auto t0 = std::chrono::steady_clock::now();
            const long long segment_events = sim.advanceToNativeTime(target_native_time);
            const auto t1 = std::chrono::steady_clock::now();
            const double wall_segment = std::chrono::duration<double>(t1 - t0).count();
            cumulative_wall += wall_segment;

            const std::string snap = snapshot_name(requested_mcs);
            sim.writeSnapshot(args.output_dir / "snapshots" / snap);
            write_progress_row(
                progress, static_cast<int>(idx), requested_mcs, target_native_time, sim.getTime(),
                sim.executedMoves(), segment_events, wall_segment, cumulative_wall,
                std::string("snapshots/") + snap, sim.nA(), sim.nTotal(),
                sim.countActiveBonds(), sim.totalGeneratorRate());
            progress.flush();

            if (sim.nA() != static_cast<long long>(std::llround(args.composition_a * sim.nTotal())))
                throw std::runtime_error("A-count conservation failure");

            std::cout << "checkpoint common_MCS=" << requested_mcs
                      << ": events=" << sim.executedMoves()
                      << ", last_event_time=" << std::setprecision(12) << sim.getTime()
                      << ", evolution_wall=" << std::fixed << std::setprecision(6)
                      << cumulative_wall << "s\n";
        }

        if (args.validation_events > 0) {
            std::ofstream events(args.output_dir / "validation_events.csv");
            if (!events) throw std::runtime_error("Cannot open validation_events.csv");
            events << "event_index,native_time,start_x,start_y,start_z,dest_x,dest_y,dest_z,selected_rate,object_total_rate\n";
            for (int i = 0; i < args.validation_events; ++i) {
                ExecutedEventRecord rec;
                if (!sim.executeOne(&rec)) throw std::runtime_error("No executable event during validation");
                if (args.validate_rates_each_event && !sim.validateAllStoredOutgoingRates())
                    throw std::runtime_error("KMC_Lattice recalculation left a stale object rate");
                events << rec.index << ',' << std::setprecision(17) << rec.native_time << ','
                       << rec.start.x << ',' << rec.start.y << ',' << rec.start.z << ','
                       << rec.dest.x << ',' << rec.dest.y << ',' << rec.dest.z << ','
                       << rec.selected_rate << ',' << rec.object_total_rate << '\n';
            }
            const fs::path validation_final = args.output_dir / "snapshots" / "validation_final.bin";
            sim.writeSnapshot(validation_final);
        }

        std::ofstream summary(args.output_dir / "summary.txt");
        if (!summary) throw std::runtime_error("Cannot open summary.txt");
        summary << std::setprecision(17);
        summary << "schema_version=1\n";
        summary << "adapter_code=kmc_lattice_" << args.recalc_mode << "\n";
        summary << "upstream_version=v2.1.0\n";
        summary << "fcc_cells=" << args.fcc_cells << "\n";
        summary << "period=" << sim.period() << "\n";
        summary << "N_total=" << sim.nTotal() << "\n";
        summary << "N_A=" << sim.nA() << "\n";
        summary << "composition_A_requested=" << args.composition_a << "\n";
        summary << "kT=" << args.kt << "\n";
        summary << "Ea=" << args.ea << "\n";
        summary << "seed=" << args.seed << "\n";
        summary << "common_mcs_per_native_time=" << COMMON_MCS_PER_NATIVE_TIME << "\n";
        summary << "executed_moves=" << sim.executedMoves() << "\n";
        summary << "native_last_event_time=" << sim.getTime() << "\n";
        summary << "active_bonds_final=" << sim.countActiveBonds() << "\n";
        summary << "total_rate_final=" << sim.totalGeneratorRate() << "\n";
        summary << "recalc_mode=" << args.recalc_mode << "\n";
        summary << "recalc_cutoff_lattice_units=3\n";
        summary << "global_event_selection=KMC_Lattice_chooseNextEvent_min_element\n";
        summary << "local_pathway_selection=KMC_Lattice_determinePathway_BKL\n";

        std::cout << "KMC_Lattice FCC Kawasaki run (" << args.recalc_mode << "): PASS\n";
        return 0;
    }
    catch (const std::exception& exc) {
        std::cerr << "ERROR: " << exc.what() << "\n";
        return 2;
    }
}
