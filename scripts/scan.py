#!/usr/bin/env python
'''This pickle script is intended to be used via apitrace's "pickle" command:
    apitrace pickle TRACE.trace | python scan.py
It will count frames and elements.'''

import optparse
import os
import sys
import time

# local library files are in this directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import tracelib

class ScanTrace(tracelib.Unpickler):

    def __init__(self, stream):
        tracelib.Unpickler.__init__(self, stream)
        self.numCalls = 0
        self.frameEnds = []

    def handleCall(self, call):
        if call.flags & tracelib.CALL_FLAG_END_FRAME:
            self.frameEnds.append(self.numCalls)
        self.numCalls += 1

def main():
    stream = sys.stdin
    optparser = optparse.OptionParser(
        usage="\n\tapitrace pickle <trace> | %prog [options]\nCount the number of frames and elements in a trace")
    optparser.add_option(
        '-p', '--profile',
        action="store_true", dest="profile", default=False,
        help="profile parsing performance")
    optparser.add_option(
        '-v', '--verbose',
        action="store_true", dest="verbose", default=False,
        help="verbose output")
    optparser.add_option('-f', '--file',
        dest="filename",
        help="specifies a pickle file to use as input instead of stdin")

    (options, args) = optparser.parse_args(sys.argv[1:])

    if args:
        optparser.error('unexpected arguments')

    if options.filename:
        stream = open(options.filename)

    startTime = time.time()
    parser = ScanTrace(stream)
    parser.parse()
    stopTime = time.time()
    duration = stopTime - startTime

    if options.filename:
        stream.close()

    if options.profile:
        sys.stderr.write('Processed %u calls in %.03f secs, at %u calls/sec\n' % (parser.numCalls, duration, parser.numCalls/duration))

    # Print out the collected information
    print "%s frames" % len(parser.frameEnds)
    print "%s calls" % parser.numCalls
    for index, frameEnd in enumerate(parser.frameEnds):
       print "frame %s ends at call %s" % (index, frameEnd)

if __name__ == '__main__':
    main()
