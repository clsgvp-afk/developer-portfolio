using System.Globalization;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace Lagerverwaltung;

public partial class MainWindow : Window
{
    private readonly LagerApi? api;
    private readonly DispatcherTimer searchTimer = new() { Interval = TimeSpan.FromMilliseconds(240) };
    private Product[] catalogue = [];
    private Booking? pending;
    private bool busy;
    private bool rebinding;

    private Product? Selected => Products.SelectedItem as Product;

    public MainWindow()
    {
        InitializeComponent();
        searchTimer.Tick += SearchTick;
        var address = Environment.GetEnvironmentVariable("LAGER_API_URL") ?? "http://127.0.0.1:8077/";
        if (Uri.TryCreate(address.TrimEnd('/') + "/", UriKind.Absolute, out var url)
            && (url.Scheme == "https" || (url.Scheme == "http" && url.IsLoopback))
            && url.UserInfo.Length == 0 && url.Query.Length == 0 && url.Fragment.Length == 0)
        {
            api = new LagerApi(url);
            Endpoint.Text = "API · " + url.GetLeftPart(UriPartial.Path);
        }
        else
        {
            Endpoint.Text = "API-Adresse ungültig";
        }
        SyncControls();
    }

    private void SyncControls()
    {
        bool canBook = !busy && api is not null && pending is null && Selected is not null;
        Refresh.IsEnabled = !busy && api is not null;
        Products.IsEnabled = Search.IsEnabled = !busy;
        BookingFields.IsEnabled = Book.IsEnabled = canBook;
        Retry.Visibility = pending is null ? Visibility.Collapsed : Visibility.Visible;
        Retry.IsEnabled = !busy;
        SelectedLabel.Text = Selected is { } item
            ? $"{item.Sku} · {item.Name} · Bestand {item.Stock}" + (item.LowStock ? " · Unter Mindestbestand" : "")
            : "Bitte einen Artikel auswählen.";
    }

    private void ApplySearch(int? keep)
    {
        rebinding = true;
        var filter = Search.Text.Trim();
        var matches = catalogue.Where(p => p.Sku.Contains(filter, StringComparison.OrdinalIgnoreCase)
            || p.Name.Contains(filter, StringComparison.CurrentCultureIgnoreCase)).ToArray();
        Products.ItemsSource = matches;
        Products.SelectedItem = matches.FirstOrDefault(p => p.Id == keep) ?? matches.FirstOrDefault();
        rebinding = false;
        SyncControls();
    }

    private async Task Run(Func<Task> work)
    {
        if (busy || api is null) return;
        busy = true;
        SyncControls();
        try { await work(); }
        catch (Exception error) when (error is HttpRequestException or TaskCanceledException or JsonException)
        {
            Status.Text = pending is null
                ? "API nicht erreichbar oder ungültige Antwort. Bitte aktualisieren."
                : "Buchung ungeklärt. „Buchung erneut prüfen“ sendet dieselbe Buchungskennung.";
        }
        finally
        {
            busy = false;
            SyncControls();
        }
    }

    private async Task LoadHistory()
    {
        History.ItemsSource = null;
        if (Selected is { } item) History.ItemsSource = await api!.Movements(item.Id);
    }

    private async Task Reload(int? keep)
    {
        catalogue = await api!.Products();
        ApplySearch(keep);
        await LoadHistory();
    }

    private Task RefreshData() => Run(async () =>
    {
        await Reload(Selected?.Id);
        Status.Text = pending is null ? $"{catalogue.Length} Artikel · Daten aktualisiert."
            : "Daten aktualisiert. Die letzte Buchung ist noch ungeklärt.";
    });

    private async void OnLoad(object sender, RoutedEventArgs e)
    {
        if (api is null) Status.Text = "LAGER_API_URL muss HTTPS oder eine lokale HTTP-Adresse verwenden.";
        else await RefreshData();
    }

    private async void RefreshClick(object sender, RoutedEventArgs e) => await RefreshData();

    private async void ProductChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!rebinding && IsLoaded) await Run(LoadHistory);
    }

    private void SearchChanged(object sender, TextChangedEventArgs e)
    {
        searchTimer.Stop();
        if (!IsLoaded) return;
        searchTimer.Start();
    }

    private async void SearchTick(object? sender, EventArgs e)
    {
        if (busy) return;
        searchTimer.Stop();
        bool restoreFocus = Search.IsKeyboardFocusWithin;
        await Run(async () =>
        {
            ApplySearch(Selected?.Id);
            await LoadHistory();
        });
        if (restoreFocus && !Search.IsKeyboardFocusWithin) Search.Focus();
    }

    private async void BookClick(object sender, RoutedEventArgs e)
    {
        if (busy || pending is not null || Selected is not { } item) return;
        if (!int.TryParse(Quantity.Text, NumberStyles.None, CultureInfo.CurrentCulture, out int quantity)
            || quantity is < 1 or > 1000000)
        {
            Status.Text = "Bitte eine ganze Menge zwischen 1 und 1.000.000 eingeben.";
            Quantity.Focus();
            return;
        }
        pending = new Booking(item.Id, Guid.NewGuid(), Direction.SelectedIndex == 0 ? quantity : -quantity, Note.Text.Trim());
        await Submit();
    }

    private async void RetryClick(object sender, RoutedEventArgs e) => await Submit();

    private Task Submit() => Run(async () =>
    {
        if (pending is not { } booking) return;
        string result;
        try
        {
            var saved = await api!.Book(booking);
            result = $"Buchung gespeichert. Bestand nach Buchung: {saved.StockAfter}.";
            Quantity.Text = "1";
            Note.Clear();
        }
        catch (Rejected error) { result = error.Message; }
        pending = null;
        try
        {
            await Reload(booking.ProductId);
            Status.Text = result;
        }
        catch (Exception error) when (error is HttpRequestException or TaskCanceledException or JsonException)
        {
            Status.Text = result + " Anzeige konnte nicht aktualisiert werden.";
        }
    });
}
