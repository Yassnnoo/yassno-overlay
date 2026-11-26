"""Силы машин Ace Of Speed 1994 (rFactor 2, мод AOS_RP_94) — сравнение по .hdv: масса, крылья, диффузор, прижим.
Цифры сняты один раз с файлов CarStat; это прикидка по параметрам, а не время круга."""

# (номер, пилот, команда, мотор, масса кг, крылья STD/EXTRA, диффузор, есть TC, прижим кгс, прижим на кг массы)
ITALY = (
    ('0', 'Igor Bolotov', 'Williams-Renault', 'Renault RS6 3.5 V10', 595, 'EXTRA', 0.63, False, 663, 1.114),
    ('2', 'Pavel Aronov', 'Williams-Renault', 'Renault RS6 3.5 V10', 595, 'EXTRA', 0.63, False, 663, 1.114),
    ('3', 'Alexey Malykhin', 'Tyrrell-Yamaha', 'Yamaha OX10A 3.5 V10', 610, 'EXTRA', 0.80, False, 713, 1.169),
    ('4', 'Jozef Chnapek', 'Tyrrell-Yamaha', 'Yamaha OX10A 3.5 V10', 615, 'EXTRA', 0.63, True, 663, 1.078),
    ('5', 'Alexey Kapustin', 'Benetton-Ford', 'Ford HBA8 3.5 V8', 595, 'STD', 0.80, False, 696, 1.170),
    ('6', 'MaX / OldWolf', 'Benetton-Ford', 'Ford HBA8 3.5 V8', 595, 'STD', 1.10, False, 784, 1.318),
    ('7', 'Aleksey Maliy', 'McLaren', 'Ford HBE7 3.5 V8', 595, 'EXTRA', 1.10, False, 802, 1.348),
    ('8', 'Artur Nikolaev', 'McLaren', 'Ford HBE7 3.5 V8', 595, 'EXTRA', 0.63, False, 663, 1.114),
    ('9', 'Vitali Babenko', 'Footwork Ford', 'Mugen-Honda MF-351H 3.5 V10', 620, 'STD', 0.63, False, 645, 1.040),
    ('10', 'Mikhail Molodkin', 'Footwork Mugen Honda', 'Mugen-Honda MF-351H 3.5 V10', 620, 'STD', 0.63, True, 645, 1.040),
    ('11', 'Denis Akinshin', 'Lotus-Ford', 'Ford HBD6 3.5 V8', 610, 'EXTRA', 0.63, False, 663, 1.087),
    ('12', 'Vladimir Lavrushkin', 'Lotus-Ford', 'Ford HBD6 3.5 V8', 610, 'STD', 0.63, False, 645, 1.057),
    ('14', 'Konstantin Panichkin', 'Jordan-Hart', 'Hart 1035 3.5 V10', 610, 'EXTRA', 0.63, True, 663, 1.087),
    ('15', 'Eugene Makarov', 'Jordan-Hart', 'Hart 1035 3.5 V10', 620, 'EXTRA', 0.63, True, 663, 1.069),
    ('19', 'Andrei Rudei', 'Larrousse-Lamborghini', 'Lamborghini 3512 3.5 V12', 610, 'EXTRA', 0.63, True, 663, 1.087),
    ('20', 'Artem Snovyda', 'Larrousse-Lamborghini', 'Lamborghini 3512 3.5 V12', 620, 'EXTRA', 0.63, True, 663, 1.069),
    ('23', 'Ansar Hadjiev', 'Minardi-Ford', 'Ford HBC6 3.5 V8', 620, 'STD', 0.63, False, 645, 1.040),
    ('24', 'Denis Gorelov', 'Minardi-Ford', 'Ford HBC6 3.5 V8', 620, 'STD', 0.63, False, 645, 1.040),
    ('25', 'Igor Maskalevich', 'Ligier-Renault', 'Renault RS5 3.5 V10', 620, 'EXTRA', 0.63, True, 663, 1.069),
    ('26', 'Alexander Dobryakov', 'Ligier-Renault', 'Renault RS5 3.5 V10', 620, 'EXTRA', 0.63, False, 663, 1.069),
    ('27', 'Vladimir Zinoviev', 'Ferrari', 'Ferrari 041 3.5 V12', 595, 'EXTRA', 0.80, False, 713, 1.198),
    ('28', 'Ahmad Masaev', 'Ferrari', 'Ferrari 041 3.5 V12', 595, 'STD', 0.80, False, 696, 1.170),
    ('29', 'Igor Bryantsev', 'Sauber', 'Sauber 2175 3.5 V10', 605, 'EXTRA', 0.80, False, 713, 1.179),
    ('30', 'Christoph Berger', 'Sauber', 'Sauber 2175 3.5 V10', 620, 'EXTRA', 0.63, True, 663, 1.069),
    ('33', 'Leon Chelidze', 'Pacific', 'Ford HBA8 3.5 V8', 610, 'EXTRA', 0.63, False, 663, 1.087),
    ('34', 'Alexey Shisha', 'Pacific', 'Ford HBA8 3.5 V8', 610, 'EXTRA', 0.63, False, 663, 1.087),
)

AOS94 = ITALY                                          # старое имя

# Гран при Бельгии — файлы машин, обновлённые 25.11.2026 (масса, крылья, диффузор; TC в .hdv больше нет)
BELGIUM = (
    ('0', 'Igor Bolotov', 'Williams-Renault', 'Renault RS6 3.5 V10', 595, 'EXTRA', 0.63, False, 663, 1.114),
    ('2', 'Pavel Aronov', 'Williams-Renault', 'Renault RS6 3.5 V10', 595, 'EXTRA', 0.63, False, 663, 1.114),
    ('3', 'Alexey Malykhin', 'Tyrrell-Yamaha', 'Yamaha OX10A 3.5 V10', 605, 'EXTRA', 0.80, False, 713, 1.179),
    ('4', 'Jozef Chnapek', 'Tyrrell-Yamaha', 'Yamaha OX10A 3.5 V10', 605, 'EXTRA', 0.63, False, 663, 1.096),
    ('5', 'Alexey Kapustin', 'Benetton-Ford', 'Ford HBA8 3.5 V8', 595, 'EXTRA', 0.80, False, 713, 1.198),
    ('6', 'MaX / OldWolf', 'Benetton-Ford', 'Ford HBA8 3.5 V8', 595, 'STD', 1.10, False, 784, 1.318),
    ('7', 'Aleksey Maliy', 'McLaren', 'Ford HBE7 3.5 V8', 595, 'EXTRA', 1.10, False, 802, 1.348),
    ('8', 'Artur Nikolaev', 'McLaren', 'Ford HBE7 3.5 V8', 595, 'EXTRA', 0.80, False, 713, 1.198),
    ('9', 'Vitali Babenko', 'Footwork Ford', 'Mugen-Honda MF-351H 3.5 V10', 610, 'STD', 0.63, False, 645, 1.057),
    ('10', 'Mikhail Molodkin', 'Footwork Mugen Honda', 'Mugen-Honda MF-351H 3.5 V10', 610, 'STD', 0.63, False, 645, 1.057),
    ('11', 'Denis Akinshin', 'Lotus-Ford', 'Ford HBD6 3.5 V8', 610, 'EXTRA', 0.63, False, 663, 1.087),
    ('12', 'Vladimir Lavrushkin', 'Lotus-Ford', 'Ford HBD6 3.5 V8', 610, 'EXTRA', 0.63, False, 663, 1.087),
    ('14', 'Konstantin Panichkin', 'Jordan-Hart', 'Hart 1035 3.5 V10', 605, 'EXTRA', 0.63, False, 663, 1.096),
    ('15', 'Eugene Makarov', 'Jordan-Hart', 'Hart 1035 3.5 V10', 605, 'EXTRA', 0.63, False, 663, 1.096),
    ('19', 'Andrei Rudei', 'Larrousse-Lamborghini', 'Lamborghini 3512 3.5 V12', 610, 'EXTRA', 0.63, False, 663, 1.087),
    ('20', 'Artem Snovyda', 'Larrousse-Lamborghini', 'Lamborghini 3512 3.5 V12', 610, 'EXTRA', 0.63, False, 663, 1.087),
    ('23', 'Ansar Hadjiev', 'Minardi-Ford', 'Ford HBC6 3.5 V8', 610, 'STD', 0.63, False, 645, 1.057),
    ('24', 'Denis Gorelov', 'Minardi-Ford', 'Ford HBC6 3.5 V8', 610, 'EXTRA', 0.63, False, 663, 1.087),
    ('25', 'Igor Maskalevich', 'Ligier-Renault', 'Renault RS5 3.5 V10', 605, 'EXTRA', 0.63, False, 663, 1.096),
    ('26', 'Alexander Dobryakov', 'Ligier-Renault', 'Renault RS5 3.5 V10', 605, 'EXTRA', 0.63, False, 663, 1.096),
    ('27', 'Vladimir Zinoviev', 'Ferrari', 'Ferrari 041 3.5 V12', 595, 'EXTRA', 0.80, False, 713, 1.198),
    ('28', 'Ahmad Masaev', 'Ferrari', 'Ferrari 041 3.5 V12', 595, 'EXTRA', 0.80, False, 713, 1.198),
    ('29', 'Igor Bryantsev', 'Sauber', 'Sauber 2175 3.5 V10', 605, 'EXTRA', 0.80, False, 713, 1.179),
    ('30', 'Christoph Berger', 'Sauber', 'Sauber 2175 3.5 V10', 605, 'EXTRA', 0.63, False, 663, 1.096),
    ('33', 'Leon Chelidze', 'Pacific', 'Ford HBA8 3.5 V8', 610, 'EXTRA', 0.63, False, 663, 1.087),
    ('34', 'Alexey Shisha', 'Pacific', 'Ford HBA8 3.5 V8', 610, 'EXTRA', 0.63, False, 663, 1.087),
)

# (название вкладки, данные) — порядок: от старой к новой
TABLES = (("Гран при Италии", ITALY), ("Гран при Бельгии", BELGIUM))

FIELDS = ("n", "driver", "team", "engine", "mass", "wings", "diffuser", "tc", "downforce", "per_kg")


def cars(table=0):
    """Список словарей по машинам выбранной таблицы (номер в TABLES), сильнейшие (прижим на кг) сверху."""
    out = [dict(zip(FIELDS, row)) for row in TABLES[table % len(TABLES)][1]]
    out.sort(key=lambda c: (-c["per_kg"], int(c["n"])))
    return out


def short_name(name):
    """«Vladimir Zinoviev» -> «V. Zinoviev»; ники и двойные имена оставляем как есть."""
    parts = name.split()
    if len(parts) == 2 and "/" not in name:
        return parts[0][:1] + ". " + parts[1]
    return name
