import re

class LefParser:
    def __init__(self, lef_file_path):
        self.lef_file_path = lef_file_path
        self.macros = {}
        self.parse()

    def parse(self):
        with open(self.lef_file_path, 'r') as f:
            content = f.read()


        macro_pattern = re.compile(r'MACRO\s+(\S+)(.*?)END\s+\1', re.DOTALL)
        size_pattern = re.compile(r'SIZE\s+([\d\.]+)\s+BY\s+([\d\.]+)')

        for match in macro_pattern.finditer(content):
            macro_name = match.group(1)
            macro_content = match.group(2)
            
            size_match = size_pattern.search(macro_content)
            if size_match:
                width = float(size_match.group(1))
                height = float(size_match.group(2))
                self.macros[macro_name] = {
                    'width': width,
                    'height': height
                }

    def get_macro_size(self, macro_name):
        return self.macros.get(macro_name)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parser = LefParser(sys.argv[1])
        print(f"Parsed {len(parser.macros)} macros.")
        for name, data in parser.macros.items():
            print(f"{name}: {data['width']} x {data['height']}")
