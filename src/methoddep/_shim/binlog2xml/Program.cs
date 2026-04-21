using Microsoft.Build.Logging.StructuredLogger;

if (args.Length != 2)
{
    Console.Error.WriteLine("usage: binlog2xml <in.binlog> <out.xml>");
    return 2;
}

var inPath = args[0];
var outPath = args[1];

if (!File.Exists(inPath))
{
    Console.Error.WriteLine($"binlog not found: {inPath}");
    return 3;
}

var build = Serialization.Read(inPath);
XmlLogWriter.WriteToXml(build, outPath);
return 0;
