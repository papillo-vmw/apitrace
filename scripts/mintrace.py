#!/usr/bin/env python
##########################################################################
#
# Copyright 2017 VMware, Inc.
# All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
##########################################################################/

"""Given an apitrace trace file and a set of trace command numbers (i.e.
a "call set" or set of image numbers), this script uses the Delta Debugging
algorithm:
   https://github.com/apitrace/apitrace/issues/433
   https://en.wikipedia.org/wiki/Delta_Debugging
   https://www.st.cs.uni-saarland.de/dd/DD.py

to find a minimal trace that will produce the exact same images in the
same order that would be produced if the original trace were replayed to
capture images with the same call set/image numbers.
"""

# Standard imports
import filecmp
import optparse
import os
import signal
import subprocess
import sys
import tempfile

# Local imports from the same directory where this script lives
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import DD # pylint: disable=wrong-import-position

##################################################################
# Exit utilities.  The "frame" is provided by the caller; we
# don't do anything with it for now.
def ExitCleanly(signalNumber, frame): # pylint: disable=unused-argument
    """This function allows the script the opportunity to clean
    up temporary files, save state, etc. when exiting due to
    a signal that can be caught.  This will exit cleanly,
    and will allow cleanup functions registered with
    atexit.register() to be executed.
    """
    sys.exit(32+signalNumber)

# These signals are commonly used to terminate a process.  Exit
# cleanly when one of these is received.
signal.signal(signal.SIGHUP, ExitCleanly)
signal.signal(signal.SIGINT, ExitCleanly)
signal.signal(signal.SIGQUIT, ExitCleanly)
signal.signal(signal.SIGTERM, ExitCleanly)

##################################################################
# We do a lot of trimming traces.  This utility encapsulates that.
# The alwaysIncludeCall specifies a call number that should always be
# included in the trimmed trace.  This is required because a trace can
# produce an image with replay snapshot even if there is no SwapBuffers
# or similar frame command to terminate the frame.  The resulting trace
# would then appear to work with testing or another process that asks
# for image snapshots, but would not be able to be played back interactively.
# By always including the final frame call, we ensure the trace works
# for playback.
def TrimTrace(sourceTraceFile, destTraceFile, options, callSet=None, truncateAtCall=None, alwaysIncludeCall=None):
    # We have to specify one of callSet or truncateAtCall, but not both.
    if callSet is not None and truncateAtCall is not None:
        raise Exception('cannot set both callSet and truncateAtCall')
    if callSet is None and truncateAtCall is None:
        raise Exception('must specify either callSet or truncateAtCall')

    # Always write our call set to a file.  Sometimes the call set will
    # fit on a command line, but a general call set will provoke an error:
    #    OSError: [Errno 7] Argument list too long
    # Note that the "with" statement will automatically close the file
    # when the block is exited.
    if truncateAtCall is not None:
        callSpecifier = '--calls=0-%s' % truncateAtCall
        if alwaysIncludeCall is not None:
            callSpecifier += ',%s' % alwaysIncludeCall
    if callSet is not None:
        tmpCallSetFile = os.path.join(options.tmpDir, 'callset.txt')
        try:
            os.remove(tmpCallSetFile)
        except OSError:
            pass
        with open(tmpCallSetFile, "w") as f:
            for call in callSet:
                f.write("%s\n" % call)
            if alwaysIncludeCall is not None:
                f.write('%s\n' % alwaysIncludeCall)
        callSpecifier = '--calls=@%s' % tmpCallSetFile

    # Ready to execute.
    executor = subprocess.Popen(
        [
            options.apitrace,
            'trim',
            callSpecifier,
            '--output=%s' % destTraceFile,
            sourceTraceFile
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, stderr = executor.communicate()

    return executor.returncode, stdout, stderr

def ReplayTrace(traceFile, callNumber, imageFile, options):
    tmpImagePrefix = os.path.join(options.tmpDir, 'image')
    executor = subprocess.Popen(
        [
            options.apitrace,
            'replay',
            # --headless doesn't seem to be working...?
            '--headless',
            '--snapshot-prefix=%s' % tmpImagePrefix,
            '--snapshot=%s' % callNumber,
            # with call-nos unset, the image appears as image 0, instead
            # of with the call number.
            '--call-nos=false',
            traceFile
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, stderr = executor.communicate()

    # The generated file will actually be named 'image0000000000.png'.
    # Rename it as the caller intended.
    try:
        os.rename('%s0000000000.png' % tmpImagePrefix, imageFile)
    except OSError:
        # trace might not have created an image
        pass

    return executor.returncode, stdout, stderr

##################################################################
# DD implementation.  The DD module requires a class based on DD.DD,
# with a _test() method that takes a set of deltas and executes
# a test using only the specified deltas.
#
# DD, which is intended to minimize the input set required to reproduce
# a bug, will attempt to find the smallest set of input that still
# produces a FAIL.  For apitrace image generation, we want the smallest
# set of input (frames or commands) that still produces a set of images.
# This means, somewhat counter-intuitively, that the "bug" we're looking
# for is the set that *creates* the expected images, so we return PASS
# if the expected images are *not* created, and FAIL if they are.  This
# may seem strange, but it lets us apply the DD algorithm to the task
# of replay minimization.

class ReduceByCommands(DD.DD):
    def __init__(self, traceFile, referenceImageFile, options, alwaysIncludeCall=None):
        DD.DD.__init__(self)
        self.traceFile = traceFile
        self.referenceImageFile = referenceImageFile
        self.alwaysIncludeCall = alwaysIncludeCall
        self.options = options
        # These constants make the below logig seem more sensical.
        self.IMAGE_CORRECT = self.FAIL
        self.IMAGE_INCORRECT = self.PASS

    def _test(self, deltas):
        # We can be called with an empty list of deltas, which confuses
        # the math below.
        if not deltas:
            return self.IMAGE_INCORRECT

        # deltas is a list of command numbers.  Create a trace
        # with just those command numbers, and see what image it
        # creates.
        deltaTraceFile = os.path.join(self.options.tmpDir, 'delta.trace')
        try:
            os.remove(deltaTraceFile)
        except OSError:
            pass
        rc, stdout, stderr = TrimTrace(self.traceFile, deltaTraceFile, self.options, callSet=deltas, alwaysIncludeCall=self.alwaysIncludeCall)

        #print "created a delta trace: rc=%s, stdout='%s', stderr='%s'" % (rc, stdout, stderr)

        # We'll want to delete the image first, if it exists - if
        # an image persists from an earlier iteration, it would confuse
        # the results.  We delete before execution instead of after so
        # that in case of error and crash, we can examine the artifacts
        # produced by the script.
        deltaImageFile = os.path.join(self.options.tmpDir, 'delta.png')
        try:
            os.remove(deltaImageFile)
        except OSError:
            pass

        # Replay the trimmed trace to produce an image file from the final
        # command in the trace.  Note that the command numbers will be
        # reordered in the trimmed file - the final command in the file will
        # have the command number equal to its index, i.e. if there are 5
        # commands in the file, they will be numbered 0-4.

        rc, stdout, stderr = ReplayTrace(deltaTraceFile, len(deltas) - 1, deltaImageFile, self.options)

        # If apitrace produced error output, then the trace isn't entirely
        # legal.  Disallow such.
        if stderr:
            print "replay of delta trace produced errors"
            return self.IMAGE_INCORRECT

        # Just one image should have been created, if all went well.
        if not os.path.isfile(deltaImageFile):
            print "replay of delta trace did not produce image file"
            return self.IMAGE_INCORRECT

        # Here, we have an image, but it may not be the same as the reference.
        if not filecmp.cmp(self.referenceImageFile, deltaImageFile, shallow=False):
            print "replay of delta trace produced incorrect image file"
            return self.IMAGE_INCORRECT

        # Here, the images are identical.
        print "replay of delta trace produced correct image file --calls=%s" % ",".join([str(delta) for delta in deltas])
        return self.IMAGE_CORRECT

##################################################################
# Main program.

def main():
    '''Main program.  Invoke with -h for help.
    '''
    program = os.path.basename(sys.argv[0])

    # Parse command line options
    optparser = optparse.OptionParser(
        usage='\n\t%prog [options] TRACE',
        version='%%prog')
    optparser.add_option(
        '-a', '--apitrace', metavar='PROGRAM',
        type='string', dest='apitrace', default='apitrace',
        help='apitrace command [default: %default]')
    optparser.add_option('-x', dest='verbose', help='increases verbosity')
    optparser.add_option(
        '-c', '--callset', dest='callset',
        help='specify the frames/call set of interest')
    optparser.add_option(
        '-t', '--tmpdir', dest='tmpDir',
        help='specify the existing working directory where temporary files will be placed')

    options, args = optparser.parse_args(sys.argv[1:])

    # The trace name should be the last argument
    if len(args) != 1:
        optparser.error("incorrect number of arguments")
    traceFile = args[0]
    if not os.path.isfile(traceFile):
        sys.stderr.write("%s: error: `%s` does not exist\n" % (program, traceFile))
        sys.exit(1)

    if not options.callset:
        optparser.error("call set not specified")

    if not options.tmpDir:
        options.cleanupTmpDir = True
        options.tmpDir = tempfile.mkdtemp(prefix="%s-" % program)
    else:
        options.cleanupTmpDir = False

    # FIXME
    # There's a quirk in apitrace in that a multithreaded trace may
    # rearrange the commands after the first "trim" (though they
    # seem to be consistent thereafter).  Right now we're assuming the
    # trace has already been given a trivial identity "trim", but
    # in the long term, we should do an identity trim explicitly
    # (unless disabled), and then try to remap the call set to account
    # for the change.

    # For each desired frame... Trim the trace after that frame (as
    # (as subsequent calls cannot affect the image).
    frameCallSet = [int(x) for x in options.callset.split(',')]

    # We'll handle the call set one frame at a time.  Merging all
    # the determined call sets together should produce a trace that
    # produces all the desired images.  Hopefully.
    for frameCall in frameCallSet:

        # Create a working trace file by trimming everything after the
        # specified call.
        trimmedTraceFile = os.path.join(options.tmpDir, "trimmed.trace")
        rc, stdout, stderr = TrimTrace(traceFile, trimmedTraceFile, options, truncateAtCall=frameCall)
        print "created a trimmed trace: rc=%s, stdout='%s', stderr='%s'" % (rc, stdout, stderr)

        # Generate a reference image for that trace.
        referenceImageFile = os.path.join(options.tmpDir, 'reference.png')
        rc, stdout, stderr = ReplayTrace(trimmedTraceFile, frameCall, referenceImageFile, options)

        # The replay of the reference image shouldn't produce errors.  If it
        # does, then we could be producing a bad image, and then trying to
        # trim to match a bad image, which is a bad idea.
        if stderr:
            raise Exception('trimmed trace "%s" produced errors on replay: %s' % (trimmedTraceFile, stderr))

        if not os.path.isfile(referenceImageFile):
            raise Exception('trimmed trace "%s" did not produce an image' % trimmedTraceFile)

        # The list of potential deltas is every command up to
        # the selected frame call, not including the selected frame call
        # itself (which will always be included in a trimmed trace).
        # range(N) yields 0...N-1, so this should be fine.
        deltas = range(frameCall)

        # Invoke the delta debugging method
        commandReducer = ReduceByCommands(trimmedTraceFile, referenceImageFile, options, alwaysIncludeCall=frameCall)
        minimalCallSet = commandReducer.ddmin(deltas)

        # Using the --calls syntax lets us pass this result back to apitrace
        # more easily
        print "Minimal set of commands to produce image %d: --calls=%s" % (frameCall, ','.join([str(call) for call in minimalCallSet]))

        # Create a trimmed trace based on that minimal call set
        minimalTraceFile = os.path.join(options.tmpDir, 'minimal-%s.trace' % frameCall)
        rc, stdout, stderr = TrimTrace(traceFile, minimalTraceFile, options, callSet=minimalCallSet, alwaysIncludeCall=frameCall)

    # end of for each specified call number

    # TODO clean up temporary directory
    if options.cleanupTmpDir:
        print "Should clean up temporary directory %s now" % options.tmpDir

# end of main()

##################################################################
if __name__ == '__main__':
    main()
