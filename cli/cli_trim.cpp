/**************************************************************************
 *
 * Copyright 2010 VMware, Inc.
 * Copyright 2011 Intel corporation
 * All Rights Reserved.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 *
 **************************************************************************/

#include <sstream>
#include <string.h>
#include <limits.h> // for CHAR_MAX
#include <getopt.h>
#include <set>

#include "cli.hpp"

#include "os_string.hpp"

#include "trace_callset.hpp"
#include "trace_parser.hpp"
#include "trace_writer.hpp"

static const char *synopsis = "Create a new trace by trimming an existing trace.";

static void
usage(void)
{
    std::cout
        << "usage: apitrace trim [OPTIONS] TRACE_FILE...\n"
        << synopsis << "\n"
        "\n"
        "    -h, --help               Show detailed help for trim options and exit\n"
        "        --calls=CALLSET      Include specified calls in the trimmed output.\n"
        "        --frames=FRAMESET    Include specified frames in the trimmed output.\n"
        "        --thread=THREAD_ID   Only retain calls from specified thread\n"
        "    -o, --output=TRACE_FILE  Output trace file\n"
    ;
}

enum {
    CALLS_OPT = CHAR_MAX + 1,
    FRAMES_OPT,
    THREAD_OPT
};

const static char *
shortOptions = "aho:x";

const static struct option
longOptions[] = {
    {"help", no_argument, 0, 'h'},
    {"calls", required_argument, 0, CALLS_OPT},
    {"frames", required_argument, 0, FRAMES_OPT},
    {"thread", required_argument, 0, THREAD_OPT},
    {"output", required_argument, 0, 'o'},
    {0, 0, 0, 0}
};

struct stringCompare {
    bool operator() (const char *a, const char *b) const {
        return strcmp(a, b) < 0;
    }
};

struct trim_options {
    /* Calls to be included in trace. */
    trace::CallSet calls;

    /* Frames to be included in trace. */
    trace::CallSet frames;

    /* Output filename */
    std::string output;

    /* Emit only calls from this thread (-1 == all threads) */
    int thread;
};

/* This utility class is useful for managing disordered traces
 * (traces with multithreaded commands where the commands are not
 * in numeric order).
 */
class ContiguousNumberTracker
{
public:
    unsigned nextExpectedNumber;
    std::set<unsigned> receivedOutOfOrder;

    ContiguousNumberTracker(void)
    {
        nextExpectedNumber = 0;
    }

    /* Add another number to the set of finished numbers, and return the
     * highest N such that all numbers from 0-N have been finish().
     */
    unsigned
    finish(unsigned n)
    {
        if (n == nextExpectedNumber) {
            nextExpectedNumber++;

            // Catch up: if there are other "finished" numbers that are
            // in queue that immediately follow this one, flush them
            // out and advance.
            while (receivedOutOfOrder.erase(nextExpectedNumber)) {
                nextExpectedNumber++;
            }
        } else {
            // Otherwise, this number is out of order.  Add this to the
            // list of finished numbers to resolve later.
            receivedOutOfOrder.insert(n);
        }

        // The caller will want to know how many contiguous numbers
        // have been finished.
        return nextExpectedNumber;
    }
};

static int
trim_trace(const char *filename, struct trim_options *options)
{
    trace::Parser p;
    unsigned frame;

    if (!p.open(filename)) {
        std::cerr << "error: failed to open " << filename << "\n";
        return 1;
    }

    /* Prepare output file and writer for output. */
    if (options->output.empty()) {
        os::String base(filename);
        base.trimExtension();

        options->output = std::string(base.str()) + std::string("-trim.trace");
    }

    trace::Writer writer;
    if (!writer.open(options->output.c_str(), p.getVersion(), p.getProperties())) {
        std::cerr << "error: failed to create " << options->output << "\n";
        return 1;
    }


    frame = 0;
    trace::Call *call;
    ContiguousNumberTracker callNumberTracker;

    while ((call = p.parse_call())) {

        /* We have to mark that we've seen all calls, even if we're going
         * to skip them below.
         */
        unsigned nextExpectedCall = callNumberTracker.finish(call->no);

        /* Choose which calls to write.  If we've asked for only calls from
         * a specified thread to be processed, skip any calls not belonging to
         * that thread.  If we've specified a callset or frameset, skip any
         * calls that aren't in one of those sets. */
        if ((options->thread == -1 || call->thread_id == options->thread) &&
            (options->calls.contains(*call) || options->frames.contains(frame, call->flags))) {
            writer.writeCall(call);
        }

        /* Keep track of frame numbers, even if the call wasn't written. */
        if (call->flags & trace::CALL_FLAG_END_FRAME) {
            frame++;
        }

        /* Done with the call object now */
        delete call;

        /* There's no use doing any work past the last call and frame
         * requested by the user.  We have to be careful about out-of-order
         * calls in the trace file,though. */
        if ((options->calls.empty() || nextExpectedCall > options->calls.getLast()) &&
            (options->frames.empty() || frame > options->frames.getLast())) {

            break;
        }
    }

    std::cerr << "Trimmed trace is available as " << options->output << "\n";

    return 0;
}

static int
command(int argc, char *argv[])
{
    struct trim_options options;

    options.calls = trace::CallSet(trace::FREQUENCY_NONE);
    options.frames = trace::CallSet(trace::FREQUENCY_NONE);
    options.output = "";
    options.thread = -1;

    int opt;
    while ((opt = getopt_long(argc, argv, shortOptions, longOptions, NULL)) != -1) {
        switch (opt) {
        case 'h':
            usage();
            return 0;
        case CALLS_OPT:
            options.calls.merge(optarg);
            break;
        case FRAMES_OPT:
            options.frames.merge(optarg);
            break;
        case THREAD_OPT:
            options.thread = atoi(optarg);
            break;
        case 'o':
            options.output = optarg;
            break;
        default:
            std::cerr << "error: unexpected option `" << (char)opt << "`\n";
            usage();
            return 1;
        }
    }

    /* If neither of --calls nor --frames was set, default to the
     * entire set of calls. */
    if (options.calls.empty() && options.frames.empty()) {
        options.calls = trace::CallSet(trace::FREQUENCY_ALL);
    }

    if (optind >= argc) {
        std::cerr << "error: apitrace trim requires a trace file as an argument.\n";
        usage();
        return 1;
    }

    if (argc > optind + 1) {
        std::cerr << "error: extraneous arguments:";
        for (int i = optind + 1; i < argc; i++) {
            std::cerr << " " << argv[i];
        }
        std::cerr << "\n";
        usage();
        return 1;
    }

    return trim_trace(argv[optind], &options);
}

const Command trim_command = {
    "trim",
    synopsis,
    usage,
    command
};
