#!/usr/bin/env python
'''This pickle script is intended to be used via apitrace's "pickle" command:
    apitrace pickle TRACE.trace | python disorder.py
It will examine a trace looking for out-of-order elements.'''

import optparse
import os
import sys
import time

# local library files are in this directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import tracelib

class IdentifyDisorder(tracelib.Unpickler):

    def __init__(self, stream):
        tracelib.Unpickler.__init__(self, stream)
        self.numCalls = 0
        self.lastCallNo = -1
        self.numDisorderedCalls = 0
        self.maxDisorderDistance = 0

    def handleCall(self, call):
        if call.no != self.lastCallNo + 1:
            print "call %s (%s) is out-of-sequence with previous call %s" % (call.no, call.functionName, self.lastCallNo)
            self.numDisorderedCalls += 1
            distance = abs(call.no - self.lastCallNo - 1)
            self.maxDisorderDistance = max(self.maxDisorderDistance, distance)
        self.lastCallNo = call.no
        self.numCalls += 1

def main():
    stream = sys.stdin
    optparser = optparse.OptionParser(
        usage="\n\tapitrace pickle <trace> | %prog [options]\nIdentify out-of-order call numbers in a trace")
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
    parser = IdentifyDisorder(stream)
    parser.parse()
    stopTime = time.time()
    duration = stopTime - startTime

    if options.filename:
        stream.close()

    if options.profile:
        sys.stderr.write('Processed %u calls in %.03f secs, at %u calls/sec\n' % (parser.numCalls, duration, parser.numCalls/duration))

    print "Number of disordered calls: %s out of %s (%s%%)" % (parser.numDisorderedCalls, parser.numCalls, 100.0 * parser.numDisorderedCalls / parser.numCalls)
    print "Max disorder distance:      %s" % parser.maxDisorderDistance

if __name__ == '__main__':
    main()
