"""
Working with named bit fields in CycLib.

CycLib supports named bits inside integer fields: declare a PAttr with
bit_defs, then read/write the bits by name. The numpy structured dtype
does not expand individual bits — only the containing integer word.
"""
import cyclib


def main() -> None:
    # 1. Declare the schema. StatusReg is a uint8 with 4 named bits:
    #    tx and rx are bits 0 and 1, then 4 reserved bits, and errFlag
    #    is bit 6.
    rule = cyclib.make_rule([
        ("Counter",   cyclib.DataType.UInt32),
        ("StatusReg", cyclib.DataType.UInt8, ["tx", "rx", "4", "errFlag"]),
    ])

    # 2. Create a buffer and (in a real scenario) fill it from a server
    #    or local producer. Here we just show the access API.
    buf = cyclib.RecBuffer(rule, capacity=100)

    # 3. A snapshot gives a structured numpy array where the whole byte
    #    StatusReg is one field.
    snap = buf.snapshot()
    print("Fields in dtype:", snap.dtype.names)  # ('Counter', 'StatusReg', ...)

    # 4. For bit-level access, use Record together with PReg.
    tx_id  = cyclib.PReg.get_id("tx")
    err_id = cyclib.PReg.get_id("errFlag")

    reader = cyclib.RecordReader(buf, batch_capacity=10)
    rec = reader.next_record()
    if rec.is_valid():
        print("tx:",      rec.get_bit(tx_id))
        print("errFlag:", rec.get_bit(err_id))

    # Alternative: mask the byte in numpy directly.
    # tx_mask = (snap["StatusReg"] & (1 << 0)).astype(bool)


if __name__ == "__main__":
    main()
