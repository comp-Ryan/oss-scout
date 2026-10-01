import click

@click.group()
def main():
    """OSS Scout: find open-source issues worth your time."""

@main.command()
def status():
    click.echo("oss-scout: no data yet")
