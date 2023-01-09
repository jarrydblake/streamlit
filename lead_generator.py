import csv

def generate_leads(filename):
    leads = []
    with open(filename, 'r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip the header row
        for row in reader:
            leads.append(row)
    return leads

def filter_leads(leads, cities, min_budget):
    filtered_leads = []
    for lead in leads:
        if lead[2] in cities and int(lead[3]) >= min_budget:
            filtered_leads.append(lead)
    return filtered_leads

def write_leads_to_file(leads, filename):
    with open(filename, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerows(leads)

leads = generate_leads('property_buyers.csv')
filtered_leads = filter_leads(leads, ['Brisbane', 'Gold Coast'], 500000)
write_leads_to_file(filtered_leads, 'filtered_property_buyers.csv')
