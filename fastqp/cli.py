#!/usr/bin/python
# pylint: disable=bad-builtin

import os
import sys
import argparse
import itertools
import math
import time
import shlex
from fastqp import FastqReader, padbases, percentile, mean, bam_read_count, gc, window
from fastqp.plots import  get_median_qual, qualplot, qualdist, qualmap, nucplot, depthplot, gcplot, gcdist, kmerplot, mismatchplot, adaptermerplot
from fastqp.adapters import all_adapter_sequences
from collections import defaultdict
from simplesam import Reader, Sam
from subprocess import Popen, PIPE
from scipy import stats
from operator import mul
from six.moves import reduce
from zipfile import ZipFile


class Bunch(object):

    def __init__(self, adict):
        self.__dict__.update(adict)


def get_metrics(file_name, quiet=False, binsize=None, nreads=2000000, count_duplicates=False,
            leftlimit=1, rightlimit=-1, kmer=5, base_probs='0.25,0.25,0.25,0.25,0.1',
            text='-', output='fastqp_figures', median_qual_r=30, fig_out=False):
    """ read FASTQ or SAM and tabulate basic metrics
    arguments is a dictionary so that we can call this as a function """
    input = argparse.FileType('r')(file_name)
    text = argparse.FileType('w')(text)
    time_start = time.time()
    in_file = os.path.basename(file_name)# filename no path
    if file_name != '<stdin>':
        bsize = os.path.getsize(file_name)
    name, ext = in_file.split('.')[0], '.'+in_file.split('.')[1]
    sample_name = name
    est_counter = int()
    sample_lengths = list()
    sample_binsizes = list()
    act_nlines = int()
    # estimate the number of lines in args.input if we can
    if ext in ['.fastq', '.fq']:
        with FastqReader(open(file_name)) as fh:
            for read in fh:
                sample_lengths.append(len(read))
                sample_binsizes.append(len(str(read)))
                est_counter += 1
                if est_counter == 10000:
                    break
            mean_bentry = mean(sample_binsizes)
            mean_len = mean(sample_lengths)
            est_nlines = int(bsize / mean_bentry)
            if not quiet:
                sys.stderr.write("At {bytes:.0f} bytes per read of {len:.0f} length "
                                 "we estimate {est:,} reads in input file.\n".format(bytes=mean_bentry,
                                                                                     len=mean_len,
                                                                                     est=est_nlines))
    elif ext == '.sam':
        with Reader(open(file_name)) as fh:
            for read in fh:
                sample_lengths.append(len(read))
                sample_binsizes.append(len(str(read)))
                est_counter += 1
                if est_counter == 10000:
                    break
            mean_bentry = mean(sample_binsizes)
            mean_len = mean(sample_lengths)
            est_nlines = int(bsize / mean_bentry)
            if not quiet:
                sys.stderr.write("At {bytes:.0f} bytes per read of {len:.0f} length "
                                 "we estimate {est:,} reads in input file.\n".format(bytes=mean_bentry,
                                                                                     len=mean_len,
                                                                                     est=est_nlines))
    elif ext == '.bam':
        est_nlines = sum(bam_read_count(file_name))
        if not quiet:
            sys.stderr.write(
                "{est:,} reads in input file.\n".format(est=est_nlines))
    elif ext == '.gz':
        if binsize:
            n = binsize
            est_nlines = None
            if not quiet:
                sys.stderr.write(
                    "Reading from gzipped file, bin size (-s) set to {binsize:n}.\n".format(binsize=n))
        else:
            sys.stderr.write(
                "Gzipped file detected. Reading file to determine bin size (-s).\n")
            p1 = Popen(shlex.split('gzip -dc %s' %
                                   file_name), stdout=PIPE)
            p2 = Popen(shlex.split('wc -l'), stdin=p1.stdout, stdout=PIPE)
            est_nlines, _ = p2.communicate()
            est_nlines = int(est_nlines) // 4
            if not quiet:
                sys.stderr.write(
                    "{est:,} reads in input file.\n".format(est=est_nlines))
    elif name == '<stdin>':
        if binsize:
            n = binsize
        else:
            n = 1
        if not quiet:
            sys.stderr.write(
                "Reading from <stdin>, bin size (-s) set to {binsize:n}.\n".format(binsize=n))
        est_nlines = None
    if est_nlines == 0:
        sys.exit("The input file appears empty. Please check the file for data.")
    elif est_nlines is not None:
        # set up factor for sampling bin size
        if binsize:
            n = binsize
        else:
            nf = math.floor(est_nlines / nreads)
            if nf >= 1:
                n = int(nf)
            else:
                n = 1
        if not quiet:
            sys.stderr.write(
                "Bin size (-s) set to {binsize:n}.\n".format(binsize=n))

    if ext in ['.sam', '.bam']:
        infile = Reader(input)
    else:
        infile = FastqReader(input, ext=ext)

    read_len = defaultdict(int)
    cycle_nuc = defaultdict(lambda: defaultdict(int))
    cycle_qual = defaultdict(lambda: defaultdict(int))
    cycle_gc = defaultdict(int)
    cycle_kmers = defaultdict(lambda: defaultdict(int))
    cycle_mismatch = {'C': defaultdict(lambda: defaultdict(int)),
                      'G': defaultdict(lambda: defaultdict(int)),
                      'A': defaultdict(lambda: defaultdict(int)),
                      'T': defaultdict(lambda: defaultdict(int))}

    if count_duplicates:
        try:
            from pybloom import ScalableBloomFilter
            bloom_filter = ScalableBloomFilter(
                mode=ScalableBloomFilter.SMALL_SET_GROWTH)
        except ImportError:
            sys.exit("--count-duplicates option requires 'pybloom' package.\n")

    duplicates = 0
    percent_complete = 10
    reads = infile.subsample(n)

    for read in reads:
        if isinstance(read, Sam):

            if args.aligned_only and not read.mapped:
                continue
            elif args.unaligned_only and read.mapped:
                continue
            if read.reverse:
                seq = read.seq[::-1]
                qual = read.qual[::-1]
            else:
                seq = read.seq
                qual = read.qual
        else:
            seq = read.seq
            qual = read.qual


        # Set up limits
        if (leftlimit == 1) and (rightlimit < 0):
            pass
        elif (leftlimit >= 1) and (rightlimit > 0):
            try:
                seq = seq[leftlimit - 1:rightlimit]
                qual = qual[leftlimit - 1:rightlimit]
            except IndexError:
                act_nlines += n
                continue

        elif (leftlimit > 1) and (rightlimit < 0):
            try:
                seq = seq[leftlimit - 1:]
                qual = qual[leftlimit - 1:]
            except IndexError:
                act_nlines += n
                continue
        if len(seq) == 0:
            act_nlines += n
            continue
        cycle_gc[gc(seq)] += 1

        if count_duplicates:
            if seq in bloom_filter:
                duplicates += 1
            else:
                bloom_filter.add(seq)

        for i, (s, q) in enumerate(zip(seq, qual)):
            cycle_nuc[leftlimit + i][s] += 1
            cycle_qual[leftlimit + i][q] += 1
        read_len[len(qual)] += 1

        # ===========buggy above seq, qual==========


        for i, k_mer in enumerate(window(seq, n=kmer)):
            cycle_kmers[leftlimit + i][k_mer] += 1

        if isinstance(read, Sam) and read.mapped:
            try:
                ref = read.parse_md()
                for i, (s, r) in enumerate(zip(seq, ref)):
                    if s != r:
                        try:
                            cycle_mismatch[r][leftlimit + i][s] += 1
                        except KeyError:
                            pass
            except KeyError:
                pass

        if est_nlines is not None:
            if (act_nlines / est_nlines) * 100 >= percent_complete:
                sys.stderr.write("Approximately {0:n}% complete at "
                                 "read {1:,} in {2}\n".format(percent_complete,
                                                              act_nlines,
                                                              time.strftime('%H:%M:%S',
                                                                            time.gmtime(time.time() - time_start))))
                percent_complete += 10
        act_nlines += n

    positions = [k for k in sorted(cycle_qual.keys())]
    depths = [read_len[k] for k in sorted(read_len.keys())]

    basecalls = [cycle_nuc[k].keys() for k in sorted(cycle_nuc.keys())]
    bases = set(list(itertools.chain.from_iterable(basecalls)))
    #nbasecalls = [ '\t'.join([str(cycle_nuc[p].get(k, 0)) for k in bases]) for p in sorted(cycle_nuc.keys())]
    map(padbases(bases), cycle_nuc.values())

    quantile_values = [0.05, 0.25, 0.5, 0.75, 0.95]
    quantiles = []
    # replace ASCII quality with integer
    for _, v in sorted(cycle_qual.items()):
        for q in tuple(v.keys()):  # py3 keys are iterator, so build a tuple to avoid recursion
            v[ord(str(q)) - 33] = v.pop(q)
        line = [percentile(v, p) for p in quantile_values]
        quantiles.append(line)

    # build kmer set of known adapter sequences
    adapter_kmers = set()
    for adapter in all_adapter_sequences:
        for k_mer in window(adapter, n=kmer):
            adapter_kmers.add(k_mer)

    # test for nonuniform kmer profiles and calculate obs/exp
    observed_expected = dict()
    all_kmers = [cycle_kmers[k].keys() for k in sorted(cycle_kmers.keys())]
    kmers = set(list(itertools.chain.from_iterable(all_kmers)))
    bad_kmers = []
    sequenced_bases = sum((l * n for l, n in read_len.items()))
    priors = tuple(map(float, base_probs.split(',')))
    for k_mer in kmers:
        kmer_counts = [(i, cycle_kmers[i][k_mer])
                       for i in sorted(cycle_kmers.keys())]
        expected_fraction = reduce(
            mul, (p ** k_mer.count(b) for b, p in zip(('A', 'T', 'C', 'G', 'N'), priors)), 1)
        expected = expected_fraction * sequenced_bases
        observed_expected[k_mer] = sum((n for _, n in kmer_counts)) / expected
        slope, _, _, p_value, _ = stats.linregress(*zip(*kmer_counts))
        if abs(slope) > 2 and p_value < 0.05:
            bad_kmers.append((k_mer, slope, p_value))
    bad_kmers = sorted(bad_kmers, key=lambda x: x[2])[:10]

    pos_gc = []
    for i in positions:
        try:
            pg = sum([cycle_nuc[i]['C'], cycle_nuc[i]['G']]) / sum([cycle_nuc[i]['C'],
                                                                    cycle_nuc[i]['G'],
                                                                    cycle_nuc[i]['A'],
                                                                    cycle_nuc[i]['T']]) * 100
        except ZeroDivisionError:
            pg = 0  # https://github.com/mdshw5/fastqp/issues/26
        pos_gc.append(pg)

    # see http://vita.had.co.nz/papers/tidy-data.pdf
    text.write("{row}\t{column}\t{pos}\t{value:n}\n".format(
        row=sample_name, column='reads', pos='None', value=act_nlines))

    for cycle, count in read_len.items():
        text.write("{row}\t{column}\t{pos:n}\t{value:n}\n".format(row=sample_name, column='read_len', pos=cycle,
                                                                       value=count))

    for i, position in enumerate(positions):
        text.write("{row}\t{column}\t{pos:n}\t{value:n}\n".format(row=sample_name,
                                                                       column='q05', pos=position,
                                                                       value=quantiles[i][0]))
        text.write("{row}\t{column}\t{pos:n}\t{value:n}\n".format(row=sample_name,
                                                                       column='q25', pos=position,
                                                                       value=quantiles[i][1]))
        text.write("{row}\t{column}\t{pos:n}\t{value:n}\n".format(row=sample_name,
                                                                       column='q50', pos=position,
                                                                       value=quantiles[i][2]))
        text.write("{row}\t{column}\t{pos:n}\t{value:n}\n".format(row=sample_name,
                                                                       column='q75', pos=position,
                                                                       value=quantiles[i][3]))
        text.write("{row}\t{column}\t{pos:n}\t{value:n}\n".format(row=sample_name,
                                                                       column='q95', pos=position,
                                                                       value=quantiles[i][4]))
    for base in bases:
        for position in positions:
            text.write("{row}\t{column}\t{pos:n}\t{value:n}\n".format(row=sample_name,
                                                                           column=base, pos=position,
                                                                           value=cycle_nuc[position][base]))
    for position in positions:
        text.write("{row}\t{column}\t{pos:n}\t{value:n}\n".format(row=sample_name,
                                                                       column='pos_gc', pos=position,
                                                                       value=pos_gc[position - 1]))
    for i in range(101):
        text.write("{row}\t{column}\t{pos:n}\t{value:n}\n".format(row=sample_name,
                                                                       column='read_gc', pos=i,
                                                                       value=cycle_gc[i]))
    for k_mer, obs_exp in sorted(observed_expected.items(), key=lambda x: x[1]):
        text.write("{row}\t{column}\t{pos}\t{value:n}\n".format(row=sample_name,
                                                                     column=k_mer, pos='None',
                                                                     value=obs_exp))

    if count_duplicates:
        text.write("{row}\t{column}\t{pos}\t{value:n}\n".format(
            row=sample_name, column='duplicate', pos='None', value=duplicates / act_nlines))
    bad_kmer_field =  [fields[0] for fields in bad_kmers]
    median_qual = get_median_qual(cycle_qual.values())

    if fig_out:
        with ZipFile(output + '.zip', mode='w') as zip_archive:
            fig_kw = {'figsize': (8, 6)}
            qualplot(positions, quantiles, zip_archive, fig_kw)
            qualdist(cycle_qual.values(), zip_archive, fig_kw)
            qualmap(cycle_qual, zip_archive, fig_kw)
            depthplot(read_len, zip_archive, fig_kw)
            gcplot(positions, pos_gc, zip_archive, fig_kw)
            gcdist(cycle_gc, zip_archive, fig_kw)
            nucplot(positions, bases, cycle_nuc, zip_archive, fig_kw)
            kmerplot(positions, cycle_kmers, zip_archive,bad_kmer_field, fig_kw)
            adaptermerplot(positions, cycle_kmers,
                           adapter_kmers, zip_archive, fig_kw)
            if isinstance(infile, Reader):
                mismatchplot(positions, cycle_mismatch, zip_archive, fig_kw)
    time_finish = time.time()
    elapsed = time_finish - time_start
    if not quiet:
        sys.stderr.write("There were {counts:,} reads in the file. Analysis \
                        finished in {sec}.\n".format(counts=act_nlines,
                        sec=time.strftime('%H:%M:%S',time.gmtime(elapsed))))

        if len(bad_kmers) > 0:
            for k_mer in bad_kmers:
                sys.stderr.write(
                    "KmerWarning: kmer %s has a non-uniform profile (slope = %s, p = %s).\n" % (k_mer))
        if median_qual < median_qual_r:
            sys.stderr.write(
                "QualityWarning: median base quality score is %s.\n" % median_qual)

    ### return metrics - this can be used to create plots
    return {'positions':positions, 'quantiles':quantiles, 'median_qual':median_qual,
            'cycle_qual':cycle_qual, 'read_len':read_len, 'pos_gc':pos_gc,
            'cycle_gc':cycle_gc, 'cycle_nuc':cycle_nuc, 'bases':bases, 'cycle_kmers':cycle_kmers,
            'bad_kmer_field':bad_kmer_field, 'adapter_kmers':adapter_kmers, 'infile':infile,
            'cycle_mismatch':cycle_mismatch}


def main(fname):
    parser = argparse.ArgumentParser(
        prog='fastqp', description="simple NGS read quality assessment using Python")
    parser.add_argument(
        'input', type=str, help="input file (one of .sam, .bam, .fq, or .fastq(.gz) or stdin (-))")
    parser.add_argument('-q', '--quiet', action="store_true", default=False,
                        help="do not print any messages (default: %(default)s)")
    parser.add_argument('-s', '--binsize', type=int,
                        help='number of reads to bin for sampling (default: auto)')
    parser.add_argument('-a', '--name', type=str,
                        help='sample name identifier for text and graphics output (default: input file name)')
    parser.add_argument('-n', '--nreads', type=int, default=2000000,
                        help='number of reads sample from input (default: %(default)s)')
    parser.add_argument('-p', '--base-probs', type=str, default='0.25,0.25,0.25,0.25,0.1',
                        help='probabilites for observing A,T,C,G,N in reads (default: %(default)s)')
    parser.add_argument('-k', '--kmer', type=int, default=5, choices=range(2, 8),
                        help='length of kmer for over-repesented kmer counts (default: %(default)s)')
    parser.add_argument('-o', '--output', type=str, default='fastqp_figures',
                        help="base name for output figures (default: %(default)s)")
    parser.add_argument('-e', '--text', type=str, default='-',
                        help="file name for text output (default: %(default)s)")
    parser.add_argument('-t', '--type', type=str, default=None,
                        choices=['fastq', 'gz', 'sam', 'bam'], help="file type (default: auto)")
    parser.add_argument('-ll', '--leftlimit', type=int, default=1,
                        help="leftmost cycle limit (default: %(default)s)")
    parser.add_argument('-rl', '--rightlimit', type=int, default=-1,
                        help="rightmost cycle limit (-1 for none) (default: %(default)s)")
    parser.add_argument('-mq', '--median-qual', type=int, default=30,
                        help="median quality threshold for failing QC (default: %(default)s)")

    align_group = parser.add_mutually_exclusive_group()
    align_group.add_argument('--aligned-only', action="store_true",
                             default=False, help="only aligned reads (default: %(default)s)")
    align_group.add_argument('--unaligned-only', action="store_true",
                             default=False, help="only unaligned reads (default: %(default)s)")
    parser.add_argument('-d', '--count-duplicates', action="store_true", default=False,
                        help="calculate sequence duplication rate (default: %(default)s)")

    # if ext_args:
    #     args = parser.parse_args(ext_args)
    # else:
    #     args = parser.parse_args()
    # arguments = vars(args)
    run(fname)

if __name__ == "__main__":
    fname = '/home/aneesh/coursera/fastq-jupyter/sra/ERR3653426.fastq'
    get_metrics(fname)
