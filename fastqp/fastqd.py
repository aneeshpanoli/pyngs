#!/usr/bin/env python3
import sys
import os
import shutil
import tempfile
import subprocess
import argparse

__version__ = "0.6.5"

def pfd(args, srr_id, extra_args):
    tmp_dir = tempfile.TemporaryDirectory(prefix="pfd_",dir=args.tmpdir)
    sys.stderr.write("tempdir: {}\n".format(tmp_dir.name))

    n_spots = get_spot_count(srr_id)
    sys.stderr.write("{} spots: {}\n".format(srr_id,n_spots))

    # minSpotId cant be lower than 1
    start = max(args.minSpotId, 1)
    # maxSpotId cant be higher than n_spots
    end = min(args.maxSpotId, n_spots) if args.maxSpotId is not None else n_spots

    blocks = split_blocks(start, end, args.threads)
    sys.stderr.write("blocks: {}\n".format(blocks))

    ps = []
    for i in range(0,args.threads):
        d = os.path.join(tmp_dir.name, str(i))
        os.mkdir(d)
        p = subprocess.Popen(["fastq-dump", "-N", str(blocks[i][0]), "-X", str(blocks[i][1]), "-O", d]+extra_args+[srr_id])
        ps.append(p)

    wfd = {}
    for i in range(0,args.threads):
        exit_code = ps[i].wait()
        if exit_code != 0:
            sys.stderr.write("fastq-dump error! exit code: {}\n".format(exit_code))
            sys.exit(1)

        tmp_path = os.path.join(tmp_dir.name, str(i))
        for fo in os.listdir(tmp_path):
            if fo not in wfd:
                wfd[fo] = open(os.path.join(args.outdir,fo), "wb")
            with open(os.path.join(tmp_path,fo), "rb") as fd:
                shutil.copyfileobj(fd, wfd[fo])

def split_blocks(start, end, n_pieces):
    total = (end-start+1)
    avg = int(total / n_pieces)
    out = []
    last = start
    for i in range(0,n_pieces):
        out.append([last,last + avg-1])
        last += avg
        if i == n_pieces-1: out[i][1] += total % n_pieces
    return out

def get_spot_count(sra_id):
    p = subprocess.Popen(["sra-stat", "--meta", "--quick", sra_id], stdout=subprocess.PIPE)
    stdout, stderr = p.communicate()
    txt = stdout.decode().rstrip().split("\n")
    total = 0
    for l in txt:
        total += int(l.split("|")[2].split(":")[0])
    return total

def partition(f, l):
    r = ([],[])
    for i in l:
        if f(i):
            r[0].append(i)
        else:
            r[1].append(i)
    return r

def main():
    parser = argparse.ArgumentParser(description="parallel fastq-dump wrapper, extra args will be passed through")
    parser.add_argument("-s","--sra-id", help="SRA id", action="append")
    parser.add_argument("-t","--threads", help="number of threads", default=1, type=int)
    parser.add_argument("-O","--outdir", help="output directory", default=".")
    parser.add_argument("--tmpdir", help="temporary directory", default=None)
    parser.add_argument("-N","--minSpotId", help="Minimum spot id", default=1, type=int)
    parser.add_argument("-X","--maxSpotId", help="Maximum spot id", default=None, type=int)
    parser.add_argument("-V", "--version", help="shows version", action="store_true")
    args, extra = parser.parse_known_args()

    if args.version:
        print("parallel-fastq-dump : {}".format(__version__))
        subprocess.Popen(["fastq-dump", "-V"]).wait()
        sys.exit(0)

    elif args.sra_id:
        extra_srrs, extra_args = partition(
            lambda s: "SRR" in s.upper() or s.lower().endswith('.sra'),
            extra)
        args.sra_id.extend(extra_srrs)
        sys.stderr.write("SRR ids: {}\n".format(args.sra_id))
        sys.stderr.write("extra args: {}\n".format(extra_args))

        if args.outdir:
            if not os.path.isdir(args.outdir):
                os.mkdir(args.outdir)

        for si in args.sra_id:
            pfd(args, si, extra_args)

    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
