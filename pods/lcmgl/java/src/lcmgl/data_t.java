/* LCM type definition class file
 * This file was automatically generated by lcm-gen
 * DO NOT MODIFY BY HAND!!!!
 */

package lcmgl;
 
import java.io.*;
import java.nio.*;
import java.util.*;
import lcm.lcm.*;
 
public final class data_t implements lcm.lcm.LCMEncodable
{
    public String name;
    public int scene;
    public int sequence;
    public int datalen;
    public byte data[];
 
    public data_t()
    {
    }
 
    public static final long LCM_FINGERPRINT;
    public static final long LCM_FINGERPRINT_BASE = 0xe4f854412b5c6ecdL;
 
    static {
        LCM_FINGERPRINT = _hashRecursive(new ArrayList<Class>());
    }
 
    public static long _hashRecursive(ArrayList<Class> classes)
    {
        if (classes.contains(lcmgl.data_t.class))
            return 0L;
 
        classes.add(lcmgl.data_t.class);
        long hash = LCM_FINGERPRINT_BASE
            ;
        classes.remove(classes.size() - 1);
        return (hash<<1) + ((hash>>63)&1);
    }
 
    public void encode(DataOutput outs) throws IOException
    {
        outs.writeLong(LCM_FINGERPRINT);
        _encodeRecursive(outs);
    }
 
    public void _encodeRecursive(DataOutput outs) throws IOException
    {
        char[] __strbuf = null;
        __strbuf = new char[this.name.length()]; this.name.getChars(0, this.name.length(), __strbuf, 0); outs.writeInt(__strbuf.length+1); for (int _i = 0; _i < __strbuf.length; _i++) outs.write(__strbuf[_i]); outs.writeByte(0); 
 
        outs.writeInt(this.scene); 
 
        outs.writeInt(this.sequence); 
 
        outs.writeInt(this.datalen); 
 
        if (this.datalen > 0)
            outs.write(this.data, 0, datalen);
 
    }
 
    public data_t(byte[] data) throws IOException
    {
        this(new LCMDataInputStream(data));
    }
 
    public data_t(DataInput ins) throws IOException
    {
        if (ins.readLong() != LCM_FINGERPRINT)
            throw new IOException("LCM Decode error: bad fingerprint");
 
        _decodeRecursive(ins);
    }
 
    public static lcmgl.data_t _decodeRecursiveFactory(DataInput ins) throws IOException
    {
        lcmgl.data_t o = new lcmgl.data_t();
        o._decodeRecursive(ins);
        return o;
    }
 
    public void _decodeRecursive(DataInput ins) throws IOException
    {
        char[] __strbuf = null;
        __strbuf = new char[ins.readInt()-1]; for (int _i = 0; _i < __strbuf.length; _i++) __strbuf[_i] = (char) (ins.readByte()&0xff); ins.readByte(); this.name = new String(__strbuf);
 
        this.scene = ins.readInt();
 
        this.sequence = ins.readInt();
 
        this.datalen = ins.readInt();
 
        this.data = new byte[(int) datalen];
        ins.readFully(this.data, 0, datalen); 
    }
 
    public lcmgl.data_t copy()
    {
        lcmgl.data_t outobj = new lcmgl.data_t();
        outobj.name = this.name;
 
        outobj.scene = this.scene;
 
        outobj.sequence = this.sequence;
 
        outobj.datalen = this.datalen;
 
        outobj.data = new byte[(int) datalen];
        if (this.datalen > 0)
            System.arraycopy(this.data, 0, outobj.data, 0, this.datalen); 
        return outobj;
    }
 
}

