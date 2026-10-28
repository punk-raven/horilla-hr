var DateFormattingUtility = DateFormattingUtility || class DateFormattingUtility {
    constructor() {
        // Default date format
        this.dateFormat = 'MMM. D, YYYY';
    }

    setDateFormat(format) {
        // Save the selected format to localStorage
        localStorage.setItem('selectedDateFormat', format);
        this.dateFormat = format;
    }

    getFormattedDate(date) {
        // var specificDate = date; // Your specific date in YYYY-MM-DD format
        // var now = moment(specificDate);
        // // console.log('LANGUAGAE DATE')
        // // console.log(now.format('LL')); // Output will be the formatted date for the specific date you provided

        if (localStorage.getItem('selectedDateFormat')){

        }
        else{
            function fetchData(callback) {

                $.ajax({
                    url: '/settings/get-date-format/',
                    method: 'GET',
                    data: { csrfmiddlewaretoken: getCookie('csrftoken') },
                    success: function(response) {
                        var date_format = response.selected_format;

                        // Call the callback function and pass the value of 'date_format'
                        callback(date_format);
                    },
                });
            }

            // Use the fetchData function with a callback
            fetchData(function(date_format) {

                // If any date format is found setting it to the local storage.
                if(date_format){
                    localStorage.setItem('selectedDateFormat', date_format);

                }
                // Setting a default date format MMM. D, YYYY
                else{
                    localStorage.setItem('selectedDateFormat', 'MMM. D, YYYY');
                }
            });

        }
        // Use the stored date format
        const storedDateFormat = localStorage.getItem('selectedDateFormat') || 'MMM. D, YYYY';


        // Preprocess the date string based on the selected format
        let processedDate = date;
        if (storedDateFormat === 'DD-MM-YYYY') {
            processedDate = date.replace(/(\d{2})-(\d{2})-(\d{4})/, '$3-$2-$1');
        } else if (storedDateFormat === 'DD.MM.YYYY') {
            processedDate = date.replace(/(\d{2})\.(\d{2})\.(\d{4})/, '$3-$2-$1');
        } else if (storedDateFormat === 'DD/MM/YYYY') {
            processedDate = date.replace(/(\d{2})\/(\d{2})\/(\d{4})/, '$3-$2-$1');
        }

        // Parse the formats this page actually receives: ISO from the
        // preprocessing above, and Django's default DATE_FORMAT ("July 30,
        // 2026", "Jan. 5, 2026"). Anything else goes through Date() exactly
        // as before, but explicitly: handing moment a non-ISO string made it
        // fall back to Date() itself and log a deprecation warning per call.
        let parsed = moment(
            processedDate,
            [moment.ISO_8601, 'MMMM D, YYYY', 'MMM. D, YYYY', 'MMM D, YYYY'],
            true
        );
        if (!parsed.isValid()) {
            parsed = moment(new Date(processedDate));
        }
        const formattedDate = parsed.format(storedDateFormat);

        return formattedDate;
    }


}

// Create an instance of the utility
var dateFormatter = dateFormatter || new DateFormattingUtility();

// Retrieve the selected date format from localStorage
var storedDateFormat = localStorage.getItem('selectedDateFormat');

if (storedDateFormat) {
    // If a date format is stored, set it in the utility
    dateFormatter.setDateFormat(storedDateFormat);
}
