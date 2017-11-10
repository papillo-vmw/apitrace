#!/usr/bin/env python
'''This apitrace pickle script examines all the blobs in a trace, in particular
looking for duplicated blobs.'''

import hashlib
import optparse
import os
import sys
import time

# local library files are in this directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import tracelib

class BlobInfo(tracelib.Unpickler):

    def __init__(self, stream):
        tracelib.Unpickler.__init__(self, stream)
        self.numCalls = 0
        self.blobs = {}

    def handleCall(self, call):
        self.numCalls += 1
        for arg in call.argValues():
            if isinstance(arg, bytearray):
                h = hashlib.sha1(arg).hexdigest()
                try:
                    self.blobs[h]['count'] += 1
                    self.blobs[h]['numbers'].append(call.no)
                except KeyError:
                    self.blobs[h] = {
                       'length': len(arg),
                       'count': 1,
                       'numbers': [call.no]
                    }

def describeBytes(numBytes, precision=0):
    suffixes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
    for suffix in suffixes:
        if numBytes < 1024:
            return "%.*f%s" % (precision, round(numBytes, precision), suffix)
        numBytes /= 1024.0
    return "way too big!"

def main():
    stream = sys.stdin
    optparser = optparse.OptionParser(
        usage="\n\tapitrace pickle <trace> | %prog [options]\nExamine blob usage in a trace, looking for multiple uses of identical blobs that could be coalesced")
    optparser.add_option(
        '-p', '--profile',
        action="store_true", dest="profile", default=False,
        help="profile parsing performance")
    optparser.add_option(
        '-v', '--verbose',
        action="store_true", dest="verbose", default=False,
        help="describe all blobs used")
    optparser.add_option('-f', '--file',
        dest="filename",
        help="specifies a pickle file to use as input instead of stdin")
    optparser.add_option('-c', '--create-callset',
        dest="callsetFilename",
        help='if specified, a callset file will be created with all the duplicate blob elements deleted.  This file can be used with "apitrace trim" to create a new trace that cannot be used for proper playback, but will give an idea of how much space can be saved by coalescing data.')

    (options, args) = optparser.parse_args(sys.argv[1:])

    if args:
        optparser.error('unexpected arguments')

    if options.filename:
        stream = open(options.filename)

    startTime = time.time()
    parser = BlobInfo(stream)
    parser.parse()
    stopTime = time.time()
    duration = stopTime - startTime

    if options.filename:
        stream.close()

    if options.profile:
        sys.stderr.write('Processed %u calls in %.03f secs, at %u calls/sec\n' % (parser.numCalls, duration, parser.numCalls/duration))

    # Look at the blob statistics, and see how much potential for space
    # savings is present
    potentialSavings = 0
    singletonBlobCount = 0
    singletonBlobLength = 0
    multipleBlobCount = 0
    multipleBlobLength = 0
    eliminateCalls = []
    for key, value in parser.blobs.iteritems():
        if value['count'] == 1:
            singletonBlobCount += 1
            singletonBlobLength += value['length']
        else:
            multipleBlobCount += 1
            multipleBlobLength += value['count'] * value['length']
            potentialSavings += (value['count'] - 1) * value['length']
            if options.verbose:
                print "blob %s (length %s) was used %s times at call numbers %s" % (
                  key,
                  value['length'],
                  value['count'],
                  value['numbers'])
            # Only have to collect the list of eliminated calls if we're
            # trying to create a callset file
            if options.callsetFilename:
                # Add all but the first to the list of calls to eliminate
                eliminateCalls.extend(value['numbers'][1:])

    # Create the callset file if requested
    if options.callsetFilename:
        eliminateCalls.sort()
        callsetFile = open(options.callsetFilename, 'w')
        # nextCallNumber always has the number of the next call to be
        # processed, either to be eliminated or included.
        nextCallNumber = 0
        for callNumber in eliminateCalls:
            # These are the expected cases for the next eliminated call number:
            #     next eliminated call = next call + 1, e.g.
            #     nextCallNumber = 6, next eliminated call = 7
            #     => Output "6", move on to process call 8
            #
            #     next eliminated call > next call, e.g.
            #     nextCallNumber = 6, next eliminated call = 10
            #     => Output "6-9", move on to process call 11.
            #
            #     next eliminated call = next call, e.g.
            #     nextCallNumber = 12, next eliminated call = 12
            #     => Output nothing, move on to process call 13
            if callNumber == nextCallNumber + 1:
                callsetFile.write('%d\n' % nextCallNumber)
                nextCallNumber = callNumber + 1
            elif callNumber > nextCallNumber:
                callsetFile.write('%d-%d\n' % (nextCallNumber, callNumber - 1))
                nextCallNumber = callNumber + 1
            elif callNumber == nextCallNumber:
                nextCallNumber = callNumber + 1
            else:
                # This shouldn't happen except in the case of programmer error
                sys.stderr.write("*** unexpected error: call numbers don't seem to be sorted (nextCallNumber=%d, callNumber=%d)" % (nextCallNumber, callNumber))

        # After all the eliminated calls are processed, we may have a set
        # of calls at the end that still need to be represented.
        if nextCallNumber < parser.numCalls:
            callsetFile.write('%d-%d\n' % (nextCallNumber, parser.numCalls))

        callsetFile.close()
        if options.verbose:
            print "created call set file %s" % options.callsetFilename

    # Summarize
    print "There are %s singleton blobs consuming a total of %s (%s bytes)" % (
        singletonBlobCount,
        describeBytes(singletonBlobLength),
        singletonBlobLength)
    print "There are %s multiple blobs consuming a total of %s (%s bytes)" % (
        multipleBlobCount,
        describeBytes(multipleBlobLength),
        multipleBlobLength)
    print "Total potential savings: %s (%s bytes)" % (
       describeBytes(potentialSavings),
       potentialSavings)

if __name__ == '__main__':
    main()
