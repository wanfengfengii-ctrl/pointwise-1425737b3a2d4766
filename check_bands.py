"""Run the original regression first, then the band business verification."""
import band_delivery
import check_project


def main():
    status = check_project.main()
    if status:
        return status
    band_delivery.main(['verify'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
