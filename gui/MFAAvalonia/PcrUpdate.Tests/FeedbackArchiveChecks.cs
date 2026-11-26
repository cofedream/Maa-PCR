using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Platform.Storage;
using MFAAvalonia.Pcr;

internal static class FeedbackArchiveChecks
{
    private static readonly MethodInfo Save = typeof(PcrLiveLogView).Assembly
        .GetType("MFAAvalonia.Pcr.PcrFeedbackActions", throwOnError: true)!
        .GetMethod("SaveArchiveAsync", BindingFlags.Static | BindingFlags.NonPublic)!;
    private static readonly byte[] Picture = Convert.FromBase64String(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGPgFlP8DwABtAFCmNjeNAAAAABJRU5ErkJggg==");

    internal static async Task Run(string root)
    {
        var directory = Directory.CreateDirectory(Path.Combine(root, "feedback-atomic-" + Guid.NewGuid().ToString("N")));
        var path = Path.Combine(directory.FullName, "玩家反馈.zip");
        var original = Archive("旧反馈", picture: false);
        var updated = Archive("新反馈", picture: true);
        File.WriteAllBytes(path, original);
        var oldHash = SHA256.HashData(original);

        Task<string> SaveTo(Uri uri, Func<string, Stream>? create = null)
        {
            var target = DispatchProxy.Create<IStorageFile, FeedbackArchiveStorageProxy>();
            ((FeedbackArchiveStorageProxy)(object)target).Uri = uri;
            return (Task<string>)Save.Invoke(null, new object?[] { target, updated, create })!;
        }

        foreach (var failure in new[] { "write", "flush" })
        {
            try
            {
                await SaveTo(new Uri(path), temporary => new FaultingFile(temporary, failure));
                throw new InvalidOperationException("Feedback export unexpectedly succeeded after " + failure);
            }
            catch (IOException ex) when (ex.Message == failure) { }
            if (!SHA256.HashData(File.ReadAllBytes(path)).SequenceEqual(oldHash)
                || directory.GetFiles().Length != 1)
                throw new InvalidOperationException("Failed feedback export changed the old ZIP or left a partial file");
        }
        try
        {
            await SaveTo(new Uri("content://documents/feedback.zip"));
            throw new InvalidOperationException("Feedback export accepted a non-local destination");
        }
        catch (IOException ex) when (ex.Message.Contains("本机文件夹", StringComparison.Ordinal)) { }

        foreach (var destination in new[] { path, Path.Combine(directory.FullName, "新建.zip") })
        {
            if (await SaveTo(new Uri(destination)) != destination
                || !File.ReadAllBytes(destination).SequenceEqual(updated))
                throw new InvalidOperationException("Feedback export did not publish the complete archive");
            using var zip = ZipFile.OpenRead(destination);
            using var text = new StreamReader(zip.GetEntry("feedback.txt")!.Open(), Encoding.UTF8);
            using var png = zip.GetEntry("selected.png")!.Open();
            using var bytes = new MemoryStream();
            png.CopyTo(bytes);
            if (zip.Entries.Count != 2 || text.ReadToEnd() != "新反馈" || !bytes.ToArray().SequenceEqual(Picture))
                throw new InvalidOperationException("Exported feedback or selected image did not round-trip");
        }
    }

    private static byte[] Archive(string text, bool picture)
    {
        using var output = new MemoryStream();
        using (var zip = new ZipArchive(output, ZipArchiveMode.Create, leaveOpen: true))
        {
            using (var note = zip.CreateEntry("feedback.txt").Open())
                note.Write(Encoding.UTF8.GetBytes(text));
            if (picture)
            {
                using var image = zip.CreateEntry("selected.png").Open();
                image.Write(Picture);
            }
        }
        return output.ToArray();
    }

    private sealed class FaultingFile(string path, string operation)
        : FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None)
    {
        public override async ValueTask WriteAsync(ReadOnlyMemory<byte> bytes, CancellationToken cancellationToken = default)
        {
            if (operation == "write")
            {
                await base.WriteAsync(bytes[..(bytes.Length / 2)], cancellationToken);
                throw new IOException(operation);
            }
            await base.WriteAsync(bytes, cancellationToken);
        }

        public override Task FlushAsync(CancellationToken cancellationToken) =>
            operation == "flush" ? Task.FromException(new IOException(operation)) : base.FlushAsync(cancellationToken);
    }
}

public class FeedbackArchiveStorageProxy : DispatchProxy
{
    public Uri Uri { get; set; } = null!;
    protected override object? Invoke(MethodInfo? method, object?[]? args) =>
        method?.Name == "get_Path" ? Uri : throw new InvalidOperationException("Unexpected storage access: " + method?.Name);
}
