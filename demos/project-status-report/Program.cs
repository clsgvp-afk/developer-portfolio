using System.Text.Json;

if (args.Length != 1)
{
    Console.Error.WriteLine("Usage: ProjectStatusReport <tasks.json>");
    return 2;
}

try
{
    var tasks = JsonSerializer.Deserialize<WorkItem[]>(
        File.ReadAllText(args[0]),
        new JsonSerializerOptions { PropertyNameCaseInsensitive = true });

    if (tasks is null || tasks.Any(task => task is null ||
        string.IsNullOrWhiteSpace(task.Title) ||
        task.Status is not ("planned" or "active" or "done")))
    {
        Console.Error.WriteLine("Expected tasks with a title and a planned, active or done status.");
        return 2;
    }

    Console.WriteLine($"Total: {tasks.Length} | Done: {tasks.Count(task => task.Status == "done")}");
    foreach (var task in tasks.Where(task => task.Status != "done"))
        Console.WriteLine($"[{task.Status}] {task.Title}");

    return 0;
}
catch (Exception error) when (error is IOException or JsonException or UnauthorizedAccessException)
{
    Console.Error.WriteLine(error.Message);
    return 2;
}

record WorkItem(string Title, string Status);
