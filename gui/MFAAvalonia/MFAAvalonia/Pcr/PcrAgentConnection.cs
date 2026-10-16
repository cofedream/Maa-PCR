using System;
using System.ComponentModel;
using System.Net;
using System.Runtime.InteropServices;

namespace MFAAvalonia.Pcr;

// Observe the owned child's socket without entering MaaAgent's socket mutex.
internal static class PcrAgentConnection
{
    internal readonly record struct Peer(int ProcessId, int Port);
    private delegate uint TableReader(IntPtr table, ref uint size, bool order, int family, int tableClass, uint reserved);
    private static TableReader readTable = GetExtendedTcpTable;
    internal static int EstablishedPort(int processId, int serverPort)
        => EstablishedConnection([processId], serverPort).Port;
    internal static Peer EstablishedConnection(int[] processIds, int serverPort)
    {
        uint size = 0;
        var result = readTable(IntPtr.Zero, ref size, false, 2, 5, 0);
        if (result != 122 || size < 4) throw new Win32Exception((int)result, "TCP owner table size");
        IntPtr table = IntPtr.Zero;
        try
        {
            // Other applications can grow the table between sizing and reading.
            for (var attempt = 0; attempt < 3; attempt++)
            {
                if (table != IntPtr.Zero) Marshal.FreeHGlobal(table);
                table = IntPtr.Zero;
                table = Marshal.AllocHGlobal(checked((int)size));
                result = readTable(table, ref size, false, 2, 5, 0);
                if (result != 122) break;
            }
            if (result != 0) throw new Win32Exception((int)result, "TCP owner table");
            var count = Marshal.ReadInt32(table);
            var stride = Marshal.SizeOf<Row>();
            if (count < 0 || 4L + (long)count * stride > size)
                throw new InvalidOperationException("Invalid TCP owner table");
            Peer? peer = null;
            for (var index = 0; index < count; index++)
            {
                var row = Marshal.PtrToStructure<Row>(IntPtr.Add(table, 4 + index * stride));
                if (Array.IndexOf(processIds, row.Pid) < 0 || row.State != 5 || Port(row.RemotePort) != serverPort
                    || !new IPAddress(row.LocalAddress).Equals(IPAddress.Loopback)
                    || !new IPAddress(row.RemoteAddress).Equals(IPAddress.Loopback)) continue;
                if (peer != null) throw new InvalidOperationException("Ambiguous owned Agent connection");
                peer = new Peer(row.Pid, Port(row.LocalPort));
            }
            return peer ?? throw new InvalidOperationException("Owned Agent TCP connection is absent");
        }
        finally { if (table != IntPtr.Zero) Marshal.FreeHGlobal(table); }
    }
    private static int Port(uint value) => (int)((value & 255) << 8 | (value >> 8 & 255));
    [StructLayout(LayoutKind.Sequential)] private struct Row
    { public uint State, LocalAddress, LocalPort, RemoteAddress, RemotePort; public int Pid; }
    [DllImport("iphlpapi.dll", SetLastError = true)]
    private static extern uint GetExtendedTcpTable(IntPtr table, ref uint size, bool order,
        int family, int tableClass, uint reserved);
}
