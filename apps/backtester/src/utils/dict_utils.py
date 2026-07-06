import re


# recursively convert dict keys from camelCase to snake_case
def convert_keys_to_snake_case(dictionary):
    new_dict = {}
    for key, value in dictionary.items():
        if isinstance(value, dict):
            new_dict[snake_case(key)] = convert_keys_to_snake_case(value)
        else:
            new_dict[snake_case(key)] = value
    return new_dict


# convert from camelCase to snake_case
def snake_case(name):
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


# recursively convert dict keys from snake_case to camelCase
def convert_keys_to_camel_case(dictionary):
    new_dict = {}
    for key, value in dictionary.items():
        if isinstance(value, dict):
            new_dict[camel_case(key)] = convert_keys_to_camel_case(value)
        else:
            new_dict[camel_case(key)] = value
    return new_dict


# convert from snake_case to camelCase
def camel_case(name):
    components = name.split("_")
    # We capitalize the first letter of each component except the first one
    # with the 'title' method and join them together.
    return components[0] + "".join(x.title() for x in components[1:])
