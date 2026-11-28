using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;

namespace Lagerverwaltung;

public record Product(int Id, string Sku, string Name, int Stock, int Minimum)
{
    public bool LowStock => Stock < Minimum;
}

public record Movement(int Id, int Delta, int StockAfter, string Note, DateTimeOffset CreatedAt)
{
    public DateTime LocalTime => CreatedAt.LocalDateTime;
}

internal record Booking(int ProductId, Guid RequestId, int Delta, string Note);
internal record ApiError(string Error, int? Stock);

internal sealed class Rejected(string message) : Exception(message);

internal sealed class LagerApi(Uri endpoint)
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower
    };

    private readonly HttpClient client = new(new HttpClientHandler { AllowAutoRedirect = false })
    {
        BaseAddress = endpoint,
        Timeout = TimeSpan.FromSeconds(8)
    };

    public Task<Product[]> Products() => List<Product>("products");
    public Task<Movement[]> Movements(int id) => List<Movement>($"products/{id}/movements");

    private async Task<T[]> List<T>(string path) =>
        await client.GetFromJsonAsync<T[]>(path, Json) ?? throw new JsonException("Leere Serverantwort.");

    public async Task<Movement> Book(Booking booking)
    {
        var payload = new { booking.RequestId, booking.Delta, booking.Note };
        using var response = await client.PostAsJsonAsync($"products/{booking.ProductId}/movements", payload, Json);
        if (response.StatusCode is HttpStatusCode.Conflict or HttpStatusCode.UnprocessableEntity or HttpStatusCode.NotFound)
        {
            var error = await response.Content.ReadFromJsonAsync<ApiError>(Json);
            throw new Rejected(error?.Error switch
            {
                "insufficient_stock" => $"Nicht genügend Bestand. Aktuell verfügbar: {error.Stock}.",
                "stock_limit" => $"Die Buchung überschreitet den Höchstbestand. Aktuell: {error.Stock}.",
                "product_not_found" => "Der Artikel ist nicht mehr vorhanden.",
                "invalid_movement" => "Der Server hat Menge oder Notiz abgelehnt.",
                "request_id_conflict" => "Die Buchungskennung wurde bereits für andere Daten verwendet.",
                _ => "Der Server hat die Buchung abgelehnt."
            });
        }
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<Movement>(Json) ?? throw new JsonException("Leere Serverantwort.");
    }
}
