using System;
using System.Formats.Tar;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Threading.Tasks;
using MFAAvalonia.Helper;
using MimeKit;

internal static class DependencyChecks
{
    internal static async Task<int> RunAsync(string evidenceDirectory)
    {
        var count = 0;
        void Require(bool result, string name)
        {
            if (!result) throw new InvalidOperationException(name);
            count++;
        }

        Require(MailboxAddress.Parse("Player <player@example.invalid>").Address == "player@example.invalid",
            "normal_mailbox_parse");
        Require(new MailboxAddress("Player", "player@example.invalid").Address == "player@example.invalid",
            "normal_mailbox_construct");
        const string injected = "\"player\r\nRCPT TO:<other@example.invalid>\r\n\"@example.invalid";
        Require(!MailboxAddress.TryParse(injected, out _), "crlf_mailbox_parse_rejected");
        var rejected = false;
        try { _ = new MailboxAddress("", injected); }
        catch (ParseException) { rejected = true; }
        catch (ArgumentException) { rejected = true; }
        Require(rejected, "crlf_mailbox_construct_rejected");

        var root = Path.Combine(evidenceDirectory, "gui-dependencies-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        foreach (var format in new[] { "zip", "tar", "tgz", "bin" })
        foreach (var asynchronous in new[] { false, true })
        foreach (var entryKind in new[] { "valid", "outside-file", "outside-directory" })
        {
            var caseRoot = Path.Combine(root, $"{format}-{asynchronous}-{entryKind}");
            Directory.CreateDirectory(caseRoot);
            var archivePath = Path.Combine(caseRoot, "input." + format);
            var destination = Path.Combine(caseRoot, "output");
            var entryName = entryKind switch
            {
                "outside-file" => "../escape.txt",
                "outside-directory" => "../escape-dir/",
                _ => "nested/payload.txt"
            };
            WriteArchive(archivePath, format, entryName, entryKind == "outside-directory");
            var result = asynchronous
                ? await UniversalExtractor.ExtractAsync(archivePath, destination)
                : UniversalExtractor.Extract(archivePath, destination);
            Require(result == (entryKind == "valid"), $"{format}_{asynchronous}_{entryKind}_result");
            if (entryKind == "valid")
                Require(File.ReadAllText(Path.Combine(destination, "nested", "payload.txt")) == "fixture-payload",
                    $"{format}_{asynchronous}_payload");
            else
                Require(!File.Exists(Path.Combine(caseRoot, "escape.txt"))
                    && !Directory.Exists(Path.Combine(caseRoot, "escape-dir")),
                    $"{format}_{asynchronous}_{entryKind}_no_escape");
        }
        Console.WriteLine($"PCR GUI dependency checks passed: {count}; network sends=0; game inputs=0");
        return count;
    }

    private static void WriteArchive(string path, string format, string entryName, bool directory)
    {
        var payload = Encoding.UTF8.GetBytes("fixture-payload");
        using var file = File.Create(path);
        if (format is "zip" or "bin")
        {
            using var archive = new ZipArchive(file, ZipArchiveMode.Create);
            if (entryName == "nested/payload.txt") archive.CreateEntry("nested/");
            var entry = archive.CreateEntry(entryName);
            if (!directory)
            {
                using var stream = entry.Open();
                stream.Write(payload);
            }
        }
        else
        {
            using var gzip = format == "tgz" ? new GZipStream(file, CompressionMode.Compress, leaveOpen: true) : null;
            using var writer = new TarWriter(gzip ?? (Stream)file, leaveOpen: true);
            if (entryName == "nested/payload.txt") writer.WriteEntry(new UstarTarEntry(TarEntryType.Directory, "nested/"));
            var entry = new UstarTarEntry(directory ? TarEntryType.Directory : TarEntryType.RegularFile, entryName);
            using var data = new MemoryStream(payload);
            if (!directory) entry.DataStream = data;
            writer.WriteEntry(entry);
        }
    }
}
