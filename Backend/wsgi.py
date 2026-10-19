from priceradar.app import create_app
app = create_app(create_tables=True)

if __name__ == "__main__":
    print("PriceRadar backend starting on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
