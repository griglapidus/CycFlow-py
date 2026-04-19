"""
Offline analysis of a .cbf file via the read_cbf_to_array helper.
"""
import sys
import cyclib


def main(path: str) -> None:
    arr = cyclib.read_cbf_to_array(path, batch_capacity=10_000)

    print(f"File:   {path}")
    print(f"Dtype:  {arr.dtype}")
    print(f"Count:  {len(arr)}")
    print(f"Memory: {arr.nbytes / 1024 / 1024:.2f} MiB\n")

    # numpy-based field stats.
    for field in arr.dtype.names or ():
        col = arr[field]
        if col.dtype.kind in "fiu":
            print(f"  {field:<16} mean={col.mean():>10.3f}  "
                  f"min={col.min():>10.3f}  max={col.max():>10.3f}")

    # Optional pandas dump.
    try:
        df = cyclib.to_dataframe(arr)
        print("\nDataFrame head:")
        print(df.head())
    except ImportError:
        pass


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python read_cbf.py <path/to/file.cbf>")
        raise SystemExit(1)
    main(sys.argv[1])
